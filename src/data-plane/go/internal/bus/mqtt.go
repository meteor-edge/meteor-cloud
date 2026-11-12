package bus

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/url"
	"os"
	"sync/atomic"
	"time"

	"github.com/eclipse/paho.golang/autopaho"
	"github.com/eclipse/paho.golang/paho"
	"github.com/google/uuid"

	"meteorcloud/dataplane/internal/envelope"
	"meteorcloud/dataplane/internal/observability"
	"meteorcloud/dataplane/internal/storage"
)

// SharedGroup load-balances client-published messages across data-plane replicas, so
// each message is ingested once.
const SharedGroup = "meteor-dataplane"

// IngressHandler receives what clients publish over MQTT. Both methods are called
// from a bounded worker pool and must return only when the message is durably
// handled (stored, acknowledged, or written to the dead-letter table): the broker
// acknowledgement is sent after they return.
type IngressHandler interface {
	Message(ctx context.Context, t Topic, payload []byte)
	CommandAck(ctx context.Context, t Topic, payload []byte)
}

// MQTTConfig configures the EMQX transport.
type MQTTConfig struct {
	BrokerURL  string // mqtts://host:8883 or mqtt://host:1883
	Username   string
	Password   string
	CACertPath string
	ClientID   string
	// IngressWorkers bounds concurrent ingestion; IngressInflight bounds unacknowledged
	// messages the broker may have outstanding to this replica (MQTT Receive Maximum).
	IngressWorkers  int
	IngressInflight uint16
}

type ingressItem struct {
	pub    *paho.Publish
	client *paho.Client
	topic  Topic
}

// MQTT is the EMQX-backed Bus. It holds two connections: one for fan-out and
// publishing, one for ingestion, so a slow store never delays live delivery.
type MQTT struct {
	cfg     MQTTConfig
	metrics *observability.Metrics
	log     *slog.Logger
	ingress IngressHandler

	fanout   *autopaho.ConnectionManager
	ingest   *autopaho.ConnectionManager
	up       atomic.Bool
	everUp   atomic.Bool
	handlers Handlers
	work     chan ingressItem
	cancel   context.CancelFunc
}

// NewMQTT creates the transport; ingress may be nil to disable MQTT ingestion.
func NewMQTT(cfg MQTTConfig, ingress IngressHandler, metrics *observability.Metrics, log *slog.Logger) *MQTT {
	if cfg.IngressWorkers <= 0 {
		cfg.IngressWorkers = 16
	}
	if cfg.IngressInflight == 0 {
		cfg.IngressInflight = 1024
	}
	if cfg.ClientID == "" {
		cfg.ClientID = "meteor-dataplane-" + uuid.NewString()[:8]
	}
	return &MQTT{cfg: cfg, ingress: ingress, metrics: metrics, log: log,
		work: make(chan ingressItem, int(cfg.IngressInflight))}
}

func (m *MQTT) tlsConfig(u *url.URL) (*tls.Config, error) {
	if u.Scheme != "mqtts" && u.Scheme != "tls" && u.Scheme != "ssl" {
		return nil, nil
	}
	cfg := &tls.Config{MinVersion: tls.VersionTLS12}
	if m.cfg.CACertPath != "" {
		pem, err := os.ReadFile(m.cfg.CACertPath)
		if err != nil {
			return nil, fmt.Errorf("read MQTT CA certificate: %w", err)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(pem) {
			return nil, errors.New("MQTT CA certificate file holds no certificate")
		}
		cfg.RootCAs = pool
	}
	return cfg, nil
}

func (m *MQTT) connect(ctx context.Context, clientID string, manualAck bool, onUp func(*autopaho.ConnectionManager),
	onMessage func(paho.PublishReceived)) (*autopaho.ConnectionManager, error) {
	u, err := url.Parse(m.cfg.BrokerURL)
	if err != nil {
		return nil, fmt.Errorf("DATAPLANE_MQTT_URL: %w", err)
	}
	tlsCfg, err := m.tlsConfig(u)
	if err != nil {
		return nil, err
	}
	if tlsCfg != nil {
		u.Scheme = "tls"
	}
	cfg := autopaho.ClientConfig{
		ServerUrls:                    []*url.URL{u},
		TlsCfg:                        tlsCfg,
		KeepAlive:                     30,
		CleanStartOnInitialConnection: true,
		SessionExpiryInterval:         0,
		ConnectTimeout:                10 * time.Second,
		ReconnectBackoff: func(attempt int) time.Duration {
			d := time.Duration(attempt) * time.Second
			if d > 15*time.Second {
				d = 15 * time.Second
			}
			return d
		},
		ConnectUsername: m.cfg.Username,
		ConnectPassword: []byte(m.cfg.Password),
		ConnectPacketBuilder: func(c *paho.Connect, _ *url.URL) (*paho.Connect, error) {
			if c.Properties == nil {
				c.Properties = &paho.ConnectProperties{}
			}
			inflight := m.cfg.IngressInflight
			c.Properties.ReceiveMaximum = &inflight
			return c, nil
		},
		OnConnectionUp: func(cm *autopaho.ConnectionManager, _ *paho.Connack) { onUp(cm) },
		OnConnectError: func(err error) {
			m.up.Store(false)
			m.metrics.BusConnected.Set(0)
			m.log.Warn("MQTT connection attempt failed", "client", clientID, "error", err.Error())
		},
		ClientConfig: paho.ClientConfig{
			ClientID:                   clientID,
			EnableManualAcknowledgment: manualAck,
			OnPublishReceived: []func(paho.PublishReceived) (bool, error){
				func(pr paho.PublishReceived) (bool, error) {
					onMessage(pr)
					return true, nil
				},
			},
			OnClientError: func(err error) {
				m.up.Store(false)
				m.metrics.BusConnected.Set(0)
				m.log.Warn("MQTT connection lost", "client", clientID, "error", err.Error())
			},
			OnServerDisconnect: func(d *paho.Disconnect) {
				m.up.Store(false)
				m.metrics.BusConnected.Set(0)
				m.log.Warn("MQTT broker disconnected the client", "client", clientID, "reason_code", d.ReasonCode)
			},
		},
	}
	return autopaho.NewConnection(ctx, cfg)
}

func subscribe(cm *autopaho.ConnectionManager, filters []string) error {
	subs := make([]paho.SubscribeOptions, len(filters))
	for i, f := range filters {
		subs[i] = paho.SubscribeOptions{Topic: f, QoS: 1}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_, err := cm.Subscribe(ctx, &paho.Subscribe{Subscriptions: subs})
	return err
}

// Start connects (retrying in the background) and installs the handlers.
func (m *MQTT) Start(ctx context.Context, h Handlers) error {
	m.handlers = h
	ctx, m.cancel = context.WithCancel(ctx)

	fanoutFilters := []string{
		TopicPrefix + "+/live/+",
		TopicPrefix + "+/apps/+/commands",
		TopicPrefix + "+/apps/+/devices/+/commands",
	}
	var err error
	m.fanout, err = m.connect(ctx, m.cfg.ClientID, false, func(cm *autopaho.ConnectionManager) {
		if err := subscribe(cm, fanoutFilters); err != nil {
			m.log.Error("MQTT fan-out subscribe failed", "error", err.Error())
			return
		}
		if m.everUp.Swap(true) {
			m.metrics.BusReconnects.Inc()
		}
		m.up.Store(true)
		m.metrics.BusConnected.Set(1)
		m.log.Info("MQTT transport connected")
	}, m.onFanout)
	if err != nil {
		return err
	}

	if m.ingress != nil {
		share := "$share/" + SharedGroup + "/" + TopicPrefix + "+/apps/+/devices/+/"
		ingressFilters := []string{share + "telemetry/+", share + "events/+", share + "inference/+", share + "commands/ack"}
		m.ingest, err = m.connect(ctx, m.cfg.ClientID+"-in", true, func(cm *autopaho.ConnectionManager) {
			if err := subscribe(cm, ingressFilters); err != nil {
				m.log.Error("MQTT ingress subscribe failed", "error", err.Error())
			}
		}, m.onIngress)
		if err != nil {
			return err
		}
		for i := 0; i < m.cfg.IngressWorkers; i++ {
			go m.ingressWorker(ctx)
		}
	}
	return nil
}

func (m *MQTT) onFanout(pr paho.PublishReceived) {
	t, ok := ParseTopic(pr.Packet.Topic)
	if !ok {
		return
	}
	switch t.Kind {
	case "live":
		var ev envelope.Event
		if json.Unmarshal(pr.Packet.Payload, &ev) != nil || ev.OrganizationID != t.OrgID {
			return
		}
		if m.handlers.OnLive != nil {
			m.handlers.OnLive(&ev)
		}
	case "commands":
		var cmd storage.Command
		if json.Unmarshal(pr.Packet.Payload, &cmd) != nil || cmd.OrganizationID != t.OrgID {
			return
		}
		if m.handlers.OnCommand != nil {
			m.handlers.OnCommand(&cmd)
		}
	}
}

// onIngress queues a client message for the worker pool. The queue is as large as
// the MQTT Receive Maximum, so the broker itself stops sending when it is full.
func (m *MQTT) onIngress(pr paho.PublishReceived) {
	t, ok := ParseTopic(pr.Packet.Topic)
	if !ok {
		pr.Client.Ack(pr.Packet)
		return
	}
	m.work <- ingressItem{pub: pr.Packet, client: pr.Client, topic: t}
}

func (m *MQTT) ingressWorker(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case item := <-m.work:
			if item.topic.Kind == "ack" {
				m.ingress.CommandAck(ctx, item.topic, item.pub.Payload)
			} else {
				m.ingress.Message(ctx, item.topic, item.pub.Payload)
			}
			// Acknowledge to the broker only now: an unacknowledged QoS 1 message is
			// redelivered after a crash, and ingestion is idempotent.
			if err := item.client.Ack(item.pub); err != nil {
				m.log.Debug("MQTT acknowledgement failed", "error", err.Error())
			}
		}
	}
}

func (m *MQTT) publish(ctx context.Context, topic string, payload []byte) error {
	if m.fanout == nil {
		return errors.New("MQTT transport is not started")
	}
	_, err := m.fanout.Publish(ctx, &paho.Publish{Topic: topic, QoS: 1, Retain: false, Payload: payload})
	return err
}

// PublishLive publishes a stored event. Never retained.
func (m *MQTT) PublishLive(ctx context.Context, ev *envelope.Event) error {
	raw, err := json.Marshal(ev)
	if err != nil {
		return err
	}
	return m.publish(ctx, LiveTopic(ev.OrganizationID, ev.Stream), raw)
}

// PublishCommand publishes a command to its target. Never retained: a one-time
// command must not be replayed to a later subscriber.
func (m *MQTT) PublishCommand(ctx context.Context, cmd *storage.Command) error {
	raw, err := json.Marshal(cmd)
	if err != nil {
		return err
	}
	return m.publish(ctx, CommandTopic(cmd.OrganizationID, cmd.Application, cmd.DeviceID), raw)
}

func (m *MQTT) Ready() bool { return m.up.Load() }

// Close disconnects cleanly.
func (m *MQTT) Close() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if m.ingest != nil {
		m.ingest.Disconnect(ctx)
	}
	if m.fanout != nil {
		m.fanout.Disconnect(ctx)
	}
	if m.cancel != nil {
		m.cancel()
	}
}
