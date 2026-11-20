// Package mqttfwd is the MQTT forwarder: it takes device messages from the
// broker (shared subscription) and hands each one to the control plane at
// POST /internal/mqtt/ingest, and it serves the small HTTP API the control plane
// uses to publish and watch topics (contracts/mqtt-http.md). It replaces the
// Python service in src/data-plane/data_plane and keeps its environment
// variables, request bodies and metric names.
package mqttfwd

import (
	"errors"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
	"time"

	"meteorcloud/dataplane/internal/natsjs"
)

// DefaultTopics are the device topics every forwarder replica takes part in,
// the same list the Python gateway subscribed to.
var DefaultTopics = []string{
	"devices/+/status",
	"devices/+/metrics",
	"devices/+/commands/result",
	"devices/+/events",
}

// Config is everything the forwarder reads from the environment. The first
// block uses the Python service's variable names so existing .env files,
// Compose, Helm and Ansible settings keep working.
type Config struct {
	ControlPlaneURL string // CONTROL_PLANE_URL
	HTTPAddr        string // HTTP_ADDR
	InternalToken   string // MQTT_INTERNAL_TOKEN (checked on our API, sent to /internal/mqtt/ingest)
	BrokerHost      string // MQTT_BROKER_HOST
	BrokerPort      int    // MQTT_BROKER_PORT
	Username        string // MQTT_PLATFORM_USERNAME
	Password        string // MQTT_PLATFORM_PASSWORD
	CACertPath      string // MQTT_CA_CERT_PATH
	Provider        string // MQTT_PROVIDER: mosquitto | emqx | vernemq
	MQTTEnabled     bool   // MQTT_ENABLED
	MetricsEnabled  bool   // METRICS_ENABLED
	LogFormat       string // LOG_FORMAT
	LogLevel        string // LOG_LEVEL

	// BrokerURL overrides host and port (mqtt:// or mqtts://). Empty means
	// mqtts://BrokerHost:BrokerPort, which is what the Python service always used.
	BrokerURL string
	// ClientID must stay the same across restarts: the broker keeps the
	// session under it (subscriptions, unacknowledged messages, and messages
	// published while the forwarder restarts) and keeps dispatching shared
	// messages to it while it is offline. A new ID on every start would leave
	// the old session collecting its share until it expires, and lose it.
	ClientID string
	// SharedGroup spreads device messages over replicas ($share/<group>/...).
	// Empty means a plain subscription: run one replica only.
	SharedGroup string
	// Workers is the number of shards. Messages of one device always go to the
	// same shard, so they reach the control plane in the order they arrived.
	Workers int
	// QueueSize bounds the messages held in memory over all shards. It is also
	// the MQTT Receive Maximum: the broker stops sending QoS 1 messages while
	// this many are unacknowledged, so a slow control plane slows the broker
	// down instead of losing messages.
	QueueSize int
	// BatchSize is how many queued messages a shard takes at once. They are
	// sent one after another over one kept-alive connection (the ingest
	// endpoint takes one message per request).
	BatchSize int
	// MaxAttempts bounds retries for answers that look permanent for this
	// message (HTTP 500 and similar). Unreachable, 401/403, 408, 429 and
	// 502-504 are retried until the control plane is back.
	MaxAttempts int
	// IngestTimeout bounds one call to /internal/mqtt/ingest.
	IngestTimeout time.Duration
	// SessionExpiry keeps the broker session across a short restart.
	SessionExpiry time.Duration
	// DrainTimeout bounds how long shutdown waits for queued messages.
	DrainTimeout time.Duration
}

// LoadConfig reads the configuration from the environment.
func LoadConfig() (Config, error) {
	e := &natsjs.Env{}
	c := Config{
		ControlPlaneURL: e.String("CONTROL_PLANE_URL", "http://127.0.0.1:8000"),
		HTTPAddr:        e.String("HTTP_ADDR", ":8081"),
		InternalToken:   e.Secret("MQTT_INTERNAL_TOKEN"),
		BrokerHost:      e.String("MQTT_BROKER_HOST", "localhost"),
		BrokerPort:      e.Int("MQTT_BROKER_PORT", 8883),
		Username:        e.String("MQTT_PLATFORM_USERNAME", "platform"),
		Password:        e.Secret("MQTT_PLATFORM_PASSWORD"),
		CACertPath:      e.String("MQTT_CA_CERT_PATH", "certs/ca.crt"),
		Provider:        e.String("MQTT_PROVIDER", "mosquitto"),
		MQTTEnabled:     boolEnv(e, "MQTT_ENABLED", true),
		MetricsEnabled:  boolEnv(e, "METRICS_ENABLED", true),
		LogFormat:       e.String("LOG_FORMAT", "console"),
		LogLevel:        e.String("LOG_LEVEL", "info"),

		BrokerURL:     e.String("MQTT_FORWARDER_BROKER_URL", ""),
		ClientID:      e.String("MQTT_FORWARDER_CLIENT_ID", "meteorcloud-mqtt-forwarder"),
		SharedGroup:   sharedGroup(),
		Workers:       e.Int("MQTT_FORWARDER_WORKERS", 16),
		QueueSize:     e.Int("MQTT_FORWARDER_QUEUE_SIZE", 4096),
		BatchSize:     e.Int("MQTT_FORWARDER_BATCH_SIZE", 32),
		MaxAttempts:   e.Int("MQTT_FORWARDER_MAX_ATTEMPTS", 10),
		IngestTimeout: e.Duration("MQTT_FORWARDER_INGEST_TIMEOUT", 10*time.Second),
		SessionExpiry: e.Duration("MQTT_FORWARDER_SESSION_EXPIRY", 5*time.Minute),
		DrainTimeout:  e.Duration("MQTT_FORWARDER_DRAIN_TIMEOUT", 20*time.Second),
	}
	if e.Err != nil {
		return c, e.Err
	}
	if c.BrokerURL == "" {
		c.BrokerURL = "mqtts://" + net.JoinHostPort(c.BrokerHost, strconv.Itoa(c.BrokerPort))
	}
	return c, c.validate()
}

func (c Config) validate() error {
	switch c.Provider {
	case "mosquitto", "emqx", "vernemq":
	default:
		return fmt.Errorf("unsupported MQTT_PROVIDER=%q; use mosquitto, emqx or vernemq", c.Provider)
	}
	switch {
	case c.InternalToken == "":
		return errors.New("MQTT_INTERNAL_TOKEN is required (the control plane rejects ingest without it)")
	case c.Workers < 1 || c.Workers > 256:
		return errors.New("MQTT_FORWARDER_WORKERS must be between 1 and 256")
	case c.QueueSize < c.Workers || c.QueueSize > 65535:
		return errors.New("MQTT_FORWARDER_QUEUE_SIZE must be at least MQTT_FORWARDER_WORKERS and at most 65535")
	case c.BatchSize < 1:
		return errors.New("MQTT_FORWARDER_BATCH_SIZE must be at least 1")
	case c.MaxAttempts < 1:
		return errors.New("MQTT_FORWARDER_MAX_ATTEMPTS must be at least 1")
	case c.IngestTimeout <= 0:
		return errors.New("MQTT_FORWARDER_INGEST_TIMEOUT must be positive")
	case c.SessionExpiry < 0 || c.SessionExpiry > 24*time.Hour:
		return errors.New("MQTT_FORWARDER_SESSION_EXPIRY must be between 0 and 24h")
	}
	return nil
}

// sharedGroup reads MQTT_FORWARDER_SHARED_GROUP. Unset means the default
// group; set to an empty value means a plain subscription.
func sharedGroup() string {
	if v, ok := os.LookupEnv("MQTT_FORWARDER_SHARED_GROUP"); ok {
		return strings.TrimSpace(v)
	}
	return "meteorcloud-mqtt-forwarder"
}

// boolEnv reads a boolean the way pydantic does (case-insensitive 1/0,
// true/false, t/f, yes/no, y/n, on/off), so values that worked for the Python
// service keep working.
func boolEnv(e *natsjs.Env, key string, def bool) bool {
	v := e.String(key, "")
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "":
		return def
	case "1", "true", "t", "yes", "y", "on":
		return true
	case "0", "false", "f", "no", "n", "off":
		return false
	default:
		if e.Err == nil {
			e.Err = fmt.Errorf("%s: %q is not a boolean", key, v)
		}
		return def
	}
}
