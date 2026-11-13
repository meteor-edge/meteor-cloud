package deviceingest

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"log/slog"
	"net/url"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/eclipse/paho.golang/autopaho"
	"github.com/eclipse/paho.golang/paho"
)

// MQTTConfig is how ingestion reaches the broker. Only standard MQTT 5 is used
// (shared subscriptions, manual acknowledgement, Receive Maximum), so any broker
// MeteorCloud supports works: Mosquitto, EMQX, VerneMQ.
type MQTTConfig struct {
	BrokerURL  string // mqtt://host:1883 or mqtts://host:8883
	Username   string // a platform login allowed to subscribe to devices/+/...
	Password   string
	CACertPath string
	ClientID   string // stable per replica, so a persistent session survives restarts
	// SharedGroup spreads messages over replicas ($share/<group>/...). Empty means a
	// plain subscription (one replica only, or every replica gets every message).
	SharedGroup string
	// Workers handle messages in parallel; Inflight is the MQTT Receive Maximum,
	// the most unacknowledged messages the broker sends before waiting.
	Workers  int
	Inflight uint16
	// SessionExpiry keeps the broker session (and unacknowledged messages) across
	// a short restart. 0 discards the session on disconnect.
	SessionExpiry time.Duration
	// DrainTimeout bounds how long shutdown waits for in-flight messages.
	DrainTimeout time.Duration
}

// Topics are the device topics ingestion subscribes to.
func (c MQTTConfig) Topics() []string {
	prefix := ""
	if c.SharedGroup != "" {
		prefix = "$share/" + c.SharedGroup + "/"
	}
	return []string{prefix + "devices/+/metrics", prefix + "devices/+/status", prefix + "devices/+/events"}
}

type work struct {
	pub    *paho.Publish
	client *paho.Client
}

// Subscriber feeds broker messages to an Ingester and acknowledges each one only
// after the Ingester has stored it on JetStream.
type Subscriber struct {
	cfg      MQTTConfig
	ingester *Ingester
	log      *slog.Logger
	metrics  *Metrics

	cm      *autopaho.ConnectionManager
	queue   chan work
	up      atomic.Bool
	workers sync.WaitGroup
	busy    atomic.Int64
}

// NewSubscriber prepares a subscriber; call Run.
func NewSubscriber(cfg MQTTConfig, in *Ingester, m *Metrics, log *slog.Logger) *Subscriber {
	if cfg.Workers <= 0 {
		cfg.Workers = 16
	}
	if cfg.Inflight == 0 {
		cfg.Inflight = 512
	}
	if cfg.DrainTimeout <= 0 {
		cfg.DrainTimeout = 20 * time.Second
	}
	return &Subscriber{cfg: cfg, ingester: in, log: log, metrics: m, queue: make(chan work, int(cfg.Inflight))}
}

// Ready reports whether the subscriber is connected and subscribed.
func (s *Subscriber) Ready() bool { return s.up.Load() }

func (s *Subscriber) tlsConfig(u *url.URL) (*tls.Config, error) {
	if u.Scheme != "mqtts" && u.Scheme != "tls" && u.Scheme != "ssl" {
		return nil, nil
	}
	cfg := &tls.Config{MinVersion: tls.VersionTLS12}
	if s.cfg.CACertPath != "" {
		pem, err := os.ReadFile(s.cfg.CACertPath)
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

// Run connects (retrying in the background), handles messages until ctx ends,
// then stops taking new messages, finishes the ones in flight (up to
// DrainTimeout) and disconnects.
func (s *Subscriber) Run(ctx context.Context) error {
	u, err := url.Parse(s.cfg.BrokerURL)
	if err != nil || u.Host == "" {
		return fmt.Errorf("MQTT broker url %q is not valid", s.cfg.BrokerURL)
	}
	tlsCfg, err := s.tlsConfig(u)
	if err != nil {
		return err
	}
	if tlsCfg != nil {
		u.Scheme = "tls"
	} else if u.Scheme == "mqtt" {
		u.Scheme = "tcp"
	}
	// Workers keep their own context so shutdown can let them finish.
	workCtx, stopWork := context.WithCancel(context.Background())
	defer stopWork()
	for i := 0; i < s.cfg.Workers; i++ {
		s.workers.Add(1)
		go s.worker(workCtx)
	}

	expiry := uint32(s.cfg.SessionExpiry / time.Second)
	inflight := s.cfg.Inflight
	connCtx, stopConn := context.WithCancel(context.Background())
	defer stopConn()
	cm, err := autopaho.NewConnection(connCtx, autopaho.ClientConfig{
		ServerUrls:                    []*url.URL{u},
		TlsCfg:                        tlsCfg,
		KeepAlive:                     30,
		CleanStartOnInitialConnection: expiry == 0,
		SessionExpiryInterval:         expiry,
		ConnectTimeout:                10 * time.Second,
		ReconnectBackoff: func(attempt int) time.Duration {
			return min(time.Duration(attempt)*time.Second, 15*time.Second)
		},
		ConnectUsername: s.cfg.Username,
		ConnectPassword: []byte(s.cfg.Password),
		ConnectPacketBuilder: func(c *paho.Connect, _ *url.URL) (*paho.Connect, error) {
			if c.Properties == nil {
				c.Properties = &paho.ConnectProperties{}
			}
			c.Properties.ReceiveMaximum = &inflight
			return c, nil
		},
		OnConnectionUp: func(cm *autopaho.ConnectionManager, _ *paho.Connack) {
			subs := make([]paho.SubscribeOptions, 0, 3)
			for _, t := range s.cfg.Topics() {
				subs = append(subs, paho.SubscribeOptions{Topic: t, QoS: 1})
			}
			sctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			if _, err := cm.Subscribe(sctx, &paho.Subscribe{Subscriptions: subs}); err != nil {
				s.log.Error("MQTT subscribe failed", "error", err.Error())
				return
			}
			s.up.Store(true)
			s.metrics.MQTTConnected.Set(1)
			s.log.Info("subscribed to device topics", "topics", s.cfg.Topics())
		},
		OnConnectError: func(err error) {
			s.up.Store(false)
			s.metrics.MQTTConnected.Set(0)
			s.log.Warn("MQTT connection attempt failed", "error", err.Error())
		},
		ClientConfig: paho.ClientConfig{
			ClientID:                   s.cfg.ClientID,
			EnableManualAcknowledgment: true,
			OnPublishReceived: []func(paho.PublishReceived) (bool, error){
				func(pr paho.PublishReceived) (bool, error) {
					// The queue is as large as Receive Maximum, so this never blocks for
					// long: the broker stops sending when the window is full.
					s.queue <- work{pub: pr.Packet, client: pr.Client}
					return true, nil
				},
			},
			OnClientError: func(err error) {
				s.up.Store(false)
				s.metrics.MQTTConnected.Set(0)
				s.log.Warn("MQTT connection lost", "error", err.Error())
			},
			OnServerDisconnect: func(d *paho.Disconnect) {
				s.up.Store(false)
				s.metrics.MQTTConnected.Set(0)
				s.log.Warn("MQTT broker disconnected ingestion", "reason_code", d.ReasonCode)
			},
		},
	})
	if err != nil {
		return err
	}
	s.cm = cm

	<-ctx.Done()
	s.log.Info("stopping: no new device messages, finishing in-flight ones")
	s.up.Store(false)
	uctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	topics := s.cfg.Topics()
	if _, err := cm.Unsubscribe(uctx, &paho.Unsubscribe{Topics: topics}); err != nil {
		s.log.Debug("MQTT unsubscribe failed", "error", err.Error())
	}
	cancel()
	deadline := time.Now().Add(s.cfg.DrainTimeout)
	for (len(s.queue) > 0 || s.busy.Load() > 0) && time.Now().Before(deadline) {
		time.Sleep(50 * time.Millisecond)
	}
	stopWork() // anything still unfinished stays unacknowledged and is redelivered
	s.workers.Wait()
	dctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cm.Disconnect(dctx)
	s.metrics.MQTTConnected.Set(0)
	return nil
}

func (s *Subscriber) worker(ctx context.Context) {
	defer s.workers.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case w := <-s.queue:
			s.busy.Add(1)
			ok := s.ingester.Handle(ctx, w.pub.Topic, w.pub.Payload)
			if ok && w.pub.QoS > 0 {
				if err := w.client.Ack(w.pub); err != nil {
					s.log.Debug("MQTT acknowledgement failed", "error", err.Error())
				}
			}
			s.busy.Add(-1)
		}
	}
}
