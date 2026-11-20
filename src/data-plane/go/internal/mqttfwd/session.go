package mqttfwd

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"net/url"
	"os"
	"sync"
	"sync/atomic"
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

	// opMu serializes every change to the broker's subscriptions (Watch,
	// Unwatch, and the subscribe after a connect), so the broker always ends up
	// with exactly the topics in watches.
	opMu sync.Mutex

	mu      sync.Mutex
	cm      *autopaho.ConnectionManager
	ready   chan struct{} // closed while connected and subscribed; replaced on disconnect
	up      bool
	watches map[string]int

	// connGen changes whenever a connection ends, so work started for one
	// connection (subscribe retries, retained replay) stops with it.
	connGen atomic.Uint64
	// lastRetained is when the last retained message arrived (Unix ns).
	lastRetained atomic.Int64
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
	if !up {
		s.connGen.Add(1)
	}
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
	if pub.Retain {
		s.lastRetained.Store(time.Now().UnixNano())
	}
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

// onConnectionUp subscribes after every (re)connect and only then marks the
// session ready. It returns when subscribed, or when this connection or the
// session ends: paho cannot reconnect while this callback runs.
func (s *Session) onConnectionUp(connCtx context.Context, cm *autopaho.ConnectionManager) {
	gen := s.connGen.Load()
	for attempt := 1; ; attempt++ {
		err := s.subscribeAll(connCtx, cm)
		if err == nil {
			break
		}
		if connCtx.Err() != nil || s.connGen.Load() != gen {
			return
		}
		s.log.Error("MQTT subscribe failed; retrying", "attempt", attempt, "error", err.Error())
		select {
		case <-connCtx.Done():
			return
		case <-time.After(min(time.Duration(attempt)*time.Second, 15*time.Second)):
		}
		if s.connGen.Load() != gen {
			return // connection lost while waiting; the next connect starts over
		}
	}
	s.log.Info("MQTT platform client connected", "topics", s.sharedTopics())
	if s.cfg.SharedGroup != "" {
		go s.replayRetained(connCtx, cm, gen) // ends with the connection
	}
}

// subscribeAll subscribes to the device topics and the watched topics, and
// marks the session ready, without a Watch or Unwatch in between.
func (s *Session) subscribeAll(ctx context.Context, cm *autopaho.ConnectionManager) error {
	s.opMu.Lock()
	defer s.opMu.Unlock()
	sctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if _, err := cm.Subscribe(sctx, &paho.Subscribe{Subscriptions: s.subscriptions()}); err != nil {
		return err
	}
	s.setUp(true)
	return nil
}

// Run connects (retrying in the background) until ctx ends, lets the pipeline
// finish what is queued (up to DrainTimeout, via drain), and disconnects.
//
// It does not leave the shared group: the broker keeps this client's session
// (SessionExpiry), with the subscriptions, the messages not yet acknowledged
// and those published while it restarts, and hands them over when the same
// client ID connects again. Leaving the group would make the broker discard
// them when this is the only member.
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
	if s.cfg.QueueSize < 1 || s.cfg.QueueSize > math.MaxUint16 {
		return fmt.Errorf("queue size %d is outside 1..%d", s.cfg.QueueSize, math.MaxUint16)
	}
	inflight := uint16(s.cfg.QueueSize)
	expirySeconds := int64(s.cfg.SessionExpiry / time.Second)
	if expirySeconds < 0 || expirySeconds > math.MaxUint32 {
		return fmt.Errorf("session expiry %v is outside 0..%ds", s.cfg.SessionExpiry, uint32(math.MaxUint32))
	}
	expiry := uint32(expirySeconds)
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
		OnConnectionUp: func(cm *autopaho.ConnectionManager, _ *paho.Connack) { s.onConnectionUp(connCtx, cm) },
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
	s.log.Info("stopping: forwarding the queued messages; the broker keeps the rest for this client ID")
	s.dropWatches(cm)
	drain(s.cfg.DrainTimeout)
	s.setUp(false)
	dctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := cm.Disconnect(dctx); err != nil {
		s.log.Debug("MQTT disconnect", "error", err.Error())
	}
	return nil
}
