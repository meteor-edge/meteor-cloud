package mqttfwd

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"log/slog"
	"net/url"
	"os"
	"slices"
	"sync"
	"time"

	"github.com/eclipse/paho.golang/autopaho"
	"github.com/eclipse/paho.golang/paho"
)

// ErrNotRunning is returned while there is no MQTT client (MQTT_ENABLED=false).
var ErrNotRunning = errors.New("MQTT client is not running.")

// errNotSubscribed mirrors the Python service's message for the same case.
var errNotSubscribed = errors.New("MQTT client is not subscribed.")

// Session is the platform MQTT client: it subscribes to the device topics,
// feeds the Pipeline, and publishes and watches topics for the HTTP API.
type Session struct {
	cfg     Config
	pipe    *Pipeline
	metrics *Metrics
	log     *slog.Logger

	mu      sync.Mutex
	cm      *autopaho.ConnectionManager
	ready   chan struct{} // closed while connected and subscribed; replaced on disconnect
	up      bool
	watches map[string]int
}

// NewSession prepares a session; call Run.
func NewSession(cfg Config, pipe *Pipeline, m *Metrics, log *slog.Logger) *Session {
	return &Session{cfg: cfg, pipe: pipe, metrics: m, log: log, ready: make(chan struct{}), watches: map[string]int{}}
}

// sharedTopics are the subscriptions for DefaultTopics, inside the shared group.
func (s *Session) sharedTopics() []string {
	out := make([]string, 0, len(DefaultTopics))
	for _, t := range DefaultTopics {
		if s.cfg.SharedGroup != "" {
			t = "$share/" + s.cfg.SharedGroup + "/" + t
		}
		out = append(out, t)
	}
	return out
}

// Ready reports whether the session is connected and subscribed.
func (s *Session) Ready() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.up
}

func (s *Session) setUp(up bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if up == s.up {
		return
	}
	s.up = up
	if up {
		close(s.ready)
		s.metrics.MQTTConnected.Set(1)
	} else {
		s.ready = make(chan struct{})
		s.metrics.MQTTConnected.Set(0)
	}
}

// waitReady waits up to d for the session to be subscribed, like the Python
// publish path (15 s) does.
func (s *Session) waitReady(ctx context.Context, d time.Duration) (*autopaho.ConnectionManager, error) {
	s.mu.Lock()
	cm, ready := s.cm, s.ready
	s.mu.Unlock()
	if cm == nil {
		return nil, ErrNotRunning
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ready:
		return cm, nil
	case <-t.C:
		return nil, errNotSubscribed
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

// Publish sends payload on topic and, for QoS 1/2, waits up to 5 s for the
// broker to confirm it.
func (s *Session) Publish(ctx context.Context, topic, payload string, qos byte, retain bool) error {
	cm, err := s.waitReady(ctx, 15*time.Second)
	if err != nil {
		return err
	}
	pctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if _, err := cm.Publish(pctx, &paho.Publish{Topic: topic, Payload: []byte(payload), QoS: qos, Retain: retain}); err != nil {
		return fmt.Errorf("MQTT publish did not complete: %w", err)
	}
	return nil
}

// Watch adds a reference to an extra topic filter (the console's live MQTT
// view) and subscribes on the first one. Messages on it are forwarded like the
// device topics. The subscription belongs to this replica only.
func (s *Session) Watch(ctx context.Context, topic string) error {
	s.mu.Lock()
	cm, up := s.cm, s.up
	if cm == nil {
		s.mu.Unlock()
		return ErrNotRunning
	}
	s.watches[topic]++
	first := s.watches[topic] == 1
	s.mu.Unlock()
	if !first || slices.Contains(DefaultTopics, topic) || !up {
		return nil // not connected: subscribed in OnConnectionUp
	}
	sctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if _, err := cm.Subscribe(sctx, &paho.Subscribe{Subscriptions: []paho.SubscribeOptions{{Topic: topic, QoS: 1}}}); err != nil {
		// Same as the Python service: logged, not returned; a reconnect retries it.
		s.log.Warn("MQTT platform subscribe request failed", "topic", topic, "error", err.Error())
	}
	return nil
}

// Unwatch drops a reference and unsubscribes on the last one.
func (s *Session) Unwatch(ctx context.Context, topic string) error {
	s.mu.Lock()
	cm, up := s.cm, s.up
	if cm == nil {
		s.mu.Unlock()
		return ErrNotRunning
	}
	last := s.watches[topic] <= 1
	if last {
		delete(s.watches, topic)
	} else {
		s.watches[topic]--
	}
	s.mu.Unlock()
	if !last || slices.Contains(DefaultTopics, topic) || !up {
		return nil
	}
	uctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if _, err := cm.Unsubscribe(uctx, &paho.Unsubscribe{Topics: []string{topic}}); err != nil {
		s.log.Warn("MQTT platform unsubscribe failed", "topic", topic, "error", err.Error())
	}
	return nil
}

// subscriptions is everything to subscribe to after (re)connecting.
func (s *Session) subscriptions() []paho.SubscribeOptions {
	var subs []paho.SubscribeOptions
	for _, t := range s.sharedTopics() {
		subs = append(subs, paho.SubscribeOptions{Topic: t, QoS: 1})
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for t := range s.watches {
		if !slices.Contains(DefaultTopics, t) {
			subs = append(subs, paho.SubscribeOptions{Topic: t, QoS: 1})
		}
	}
	return subs
}

func (s *Session) tlsConfig(u *url.URL) (*tls.Config, error) {
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

// onPublish queues a received message. It runs on paho's router goroutine.
func (s *Session) onPublish(pr paho.PublishReceived) (bool, error) {
	pub := pr.Packet
	s.metrics.MessagesReceived.WithLabelValues(messageKind(pub.Topic)).Inc()
	m := Message{Topic: pub.Topic, Payload: pub.Payload, Received: time.Now()}
	if pub.QoS > 0 {
		client := pr.Client
		m.Ack = func() {
			if err := client.Ack(pub); err != nil {
				s.log.Debug("MQTT acknowledgement failed", "error", err.Error())
			}
		}
	}
	if !s.pipe.Offer(m, pub.QoS > 0) && pub.QoS == 0 {
		s.log.Warn("QoS 0 device message dropped; forward queue full", "topic", pub.Topic)
	}
	return true, nil
}

// Run connects (retrying in the background) until ctx ends, then unsubscribes,
// lets the pipeline finish what is queued (up to DrainTimeout, via drain) and
// disconnects. Messages not forwarded by then stay unacknowledged and the
// broker sends them again, to this replica after a restart or to another one.
func (s *Session) Run(ctx context.Context, drain func(time.Duration)) error {
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
	expiry := uint32(s.cfg.SessionExpiry / time.Second)
	inflight := uint16(s.cfg.QueueSize)
	connCtx, stopConn := context.WithCancel(context.Background())
	defer stopConn()
	cm, err := autopaho.NewConnection(connCtx, autopaho.ClientConfig{
		ServerUrls:                    []*url.URL{u},
		TlsCfg:                        tlsCfg,
		KeepAlive:                     60,
		CleanStartOnInitialConnection: expiry == 0,
		SessionExpiryInterval:         expiry,
		ConnectTimeout:                10 * time.Second,
		ReconnectBackoff: func(attempt int) time.Duration {
			return min(time.Duration(attempt)*time.Second, 30*time.Second) // Python: 1 s to 30 s
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
			// Retry until subscribed: one failed Subscribe with the connection
			// still up would never call OnConnectionUp again.
			for attempt := 1; ; attempt++ {
				subs := s.subscriptions()
				sctx, cancel := context.WithTimeout(connCtx, 10*time.Second)
				_, err := cm.Subscribe(sctx, &paho.Subscribe{Subscriptions: subs})
				cancel()
				if err == nil {
					break
				}
				s.log.Error("MQTT subscribe failed; retrying", "attempt", attempt, "error", err.Error())
				select {
				case <-connCtx.Done():
					return
				case <-time.After(min(time.Duration(attempt)*time.Second, 15*time.Second)):
				}
			}
			s.setUp(true)
			s.log.Info("MQTT platform client connected", "topics", s.sharedTopics())
		},
		OnConnectError: func(err error) {
			s.setUp(false)
			s.log.Warn("MQTT platform client connect failed", "error", err.Error())
		},
		ClientConfig: paho.ClientConfig{
			ClientID:                   s.cfg.ClientID,
			EnableManualAcknowledgment: true,
			OnPublishReceived:          []func(paho.PublishReceived) (bool, error){s.onPublish},
			OnClientError: func(err error) {
				s.setUp(false)
				s.log.Warn("MQTT connection lost", "error", err.Error())
			},
			OnServerDisconnect: func(d *paho.Disconnect) {
				s.setUp(false)
				s.log.Warn("MQTT broker disconnected the forwarder", "reason_code", d.ReasonCode)
			},
		},
	})
	if err != nil {
		return err
	}
	s.mu.Lock()
	s.cm = cm
	s.mu.Unlock()

	<-ctx.Done()
	s.log.Info("stopping: no new device messages, forwarding the queued ones")
	s.setUp(false)
	uctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	topics := s.sharedTopics()
	for _, sub := range s.subscriptions()[len(topics):] {
		topics = append(topics, sub.Topic) // watches too, or the kept session would hold them
	}
	if _, err := cm.Unsubscribe(uctx, &paho.Unsubscribe{Topics: topics}); err != nil {
		s.log.Debug("MQTT unsubscribe failed", "error", err.Error())
	}
	cancel()
	drain(s.cfg.DrainTimeout)
	dctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := cm.Disconnect(dctx); err != nil {
		s.log.Debug("MQTT disconnect", "error", err.Error())
	}
	return nil
}
