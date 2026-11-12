// Package config reads the process configuration from environment variables.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is the whole service configuration. Secrets come from the environment or
// from files named by *_FILE variables; none has a usable production default.
type Config struct {
	// Listeners. Applications reach HTTPAddr (behind TLS termination). OpsAddr carries
	// metrics, readiness and the internal API and must not be exposed publicly.
	HTTPAddr string
	OpsAddr  string

	LogFormat string
	LogLevel  string

	// Policy source: the control plane, or a JSON file for local development.
	ControlPlaneURL string
	InternalToken   string
	PolicyFile      string
	PolicyInterval  time.Duration
	PolicyMaxStale  time.Duration

	// Storage: "postgres" (default) or "memory" (tests, demos; not durable).
	StorageBackend string
	DatabaseURL    string
	DatabaseConns  int
	QueryTimeout   time.Duration
	RetentionDays  int

	// Transport: "mqtt" (EMQX) or "memory" (single replica, no broker).
	BusBackend     string
	MQTTBrokerURL  string
	MQTTUsername   string
	MQTTPassword   string
	MQTTCACertPath string
	MQTTClientID   string
	// Direct MQTT access for applications (optional).
	MQTTJWTSecret string
	MQTTPublicURL string
	MQTTTokenTTL  time.Duration

	// Limits.
	MaxPayloadBytes  int
	MaxBatch         int
	MaxFutureSkew    time.Duration
	MaxEventAge      time.Duration
	RateLimitPerSec  float64
	RateLimitBurst   int
	QueueSize        int
	IngestWorkers    int
	QueryMaxLimit    int
	QueryMaxBuckets  int
	CommandAckWait   time.Duration
	ShutdownTimeout  time.Duration
	LifecycleEvents  bool
	AllowedWSOrigins []string
}

func env(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

// secret reads KEY, or the file named by KEY_FILE.
func secret(key string) (string, error) {
	if path := os.Getenv(key + "_FILE"); path != "" {
		raw, err := os.ReadFile(path)
		if err != nil {
			return "", fmt.Errorf("%s_FILE: %w", key, err)
		}
		return strings.TrimSpace(string(raw)), nil
	}
	return os.Getenv(key), nil
}

func envInt(key string, def int) (int, error) {
	v := os.Getenv(key)
	if v == "" {
		return def, nil
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", key, err)
	}
	return n, nil
}

func envDuration(key string, def time.Duration) (time.Duration, error) {
	v := os.Getenv(key)
	if v == "" {
		return def, nil
	}
	d, err := time.ParseDuration(v)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", key, err)
	}
	return d, nil
}

// Load reads and validates the configuration.
func Load() (*Config, error) {
	c := &Config{
		HTTPAddr:        env("DATAPLANE_HTTP_ADDR", ":8082"),
		OpsAddr:         env("DATAPLANE_OPS_ADDR", ":8092"),
		LogFormat:       env("LOG_FORMAT", "json"),
		LogLevel:        env("LOG_LEVEL", "info"),
		ControlPlaneURL: env("CONTROL_PLANE_URL", ""),
		PolicyFile:      env("DATAPLANE_POLICY_FILE", ""),
		StorageBackend:  env("DATAPLANE_STORAGE", "postgres"),
		BusBackend:      env("DATAPLANE_BUS", "mqtt"),
		MQTTBrokerURL:   env("DATAPLANE_MQTT_URL", ""),
		MQTTUsername:    env("MQTT_PLATFORM_USERNAME", "platform"),
		MQTTCACertPath:  env("MQTT_CA_CERT_PATH", ""),
		MQTTClientID:    env("DATAPLANE_MQTT_CLIENT_ID", ""),
		LifecycleEvents: env("DATAPLANE_LIFECYCLE_EVENTS", "true") == "true",
	}
	if origins := os.Getenv("DATAPLANE_WS_ALLOWED_ORIGINS"); origins != "" {
		c.AllowedWSOrigins = strings.Split(origins, ",")
	}
	var err error
	if c.InternalToken, err = secret("DATA_PLANE_INTERNAL_TOKEN"); err != nil {
		return nil, err
	}
	if c.DatabaseURL, err = secret("DATAPLANE_DATABASE_URL"); err != nil {
		return nil, err
	}
	if c.MQTTPassword, err = secret("MQTT_PLATFORM_PASSWORD"); err != nil {
		return nil, err
	}
	if c.MQTTJWTSecret, err = secret("DATAPLANE_MQTT_JWT_SECRET"); err != nil {
		return nil, err
	}
	c.MQTTPublicURL = env("DATAPLANE_MQTT_PUBLIC_URL", "")
	ints := []struct {
		dst *int
		key string
		def int
	}{
		{&c.DatabaseConns, "DATAPLANE_DATABASE_MAX_CONNS", 10},
		{&c.RetentionDays, "DATAPLANE_RETENTION_DAYS", 0},
		{&c.MaxPayloadBytes, "DATAPLANE_MAX_PAYLOAD_BYTES", 64 << 10},
		{&c.MaxBatch, "DATAPLANE_MAX_BATCH", 500},
		{&c.RateLimitBurst, "DATAPLANE_RATE_LIMIT_BURST", 1000},
		{&c.QueueSize, "DATAPLANE_QUEUE_SIZE", 10000},
		{&c.IngestWorkers, "DATAPLANE_INGEST_WORKERS", 4},
		{&c.QueryMaxLimit, "DATAPLANE_QUERY_MAX_LIMIT", 1000},
		{&c.QueryMaxBuckets, "DATAPLANE_QUERY_MAX_BUCKETS", 5000},
	}
	for _, i := range ints {
		if *i.dst, err = envInt(i.key, i.def); err != nil {
			return nil, err
		}
	}
	rate, err := envInt("DATAPLANE_RATE_LIMIT_PER_SECOND", 200)
	if err != nil {
		return nil, err
	}
	c.RateLimitPerSec = float64(rate)
	durations := []struct {
		dst *time.Duration
		key string
		def time.Duration
	}{
		{&c.PolicyInterval, "DATAPLANE_POLICY_INTERVAL", 5 * time.Second},
		{&c.PolicyMaxStale, "DATAPLANE_POLICY_MAX_STALE", 24 * time.Hour},
		{&c.QueryTimeout, "DATAPLANE_QUERY_TIMEOUT", 10 * time.Second},
		{&c.MaxFutureSkew, "DATAPLANE_MAX_FUTURE_SKEW", 5 * time.Minute},
		{&c.MaxEventAge, "DATAPLANE_MAX_EVENT_AGE", 30 * 24 * time.Hour},
		{&c.CommandAckWait, "DATAPLANE_COMMAND_ACK_TIMEOUT", 5 * time.Second},
		{&c.MQTTTokenTTL, "DATAPLANE_MQTT_TOKEN_TTL", time.Hour},
		{&c.ShutdownTimeout, "DATAPLANE_SHUTDOWN_TIMEOUT", 20 * time.Second},
	}
	for _, d := range durations {
		if *d.dst, err = envDuration(d.key, d.def); err != nil {
			return nil, err
		}
	}
	return c, c.validate()
}

func (c *Config) validate() error {
	if c.PolicyFile == "" {
		if c.ControlPlaneURL == "" {
			return fmt.Errorf("set CONTROL_PLANE_URL (or DATAPLANE_POLICY_FILE for local development)")
		}
		if c.InternalToken == "" {
			return fmt.Errorf("DATA_PLANE_INTERNAL_TOKEN is required")
		}
	}
	switch c.StorageBackend {
	case "postgres":
		if c.DatabaseURL == "" {
			return fmt.Errorf("DATAPLANE_DATABASE_URL is required with DATAPLANE_STORAGE=postgres")
		}
	case "memory":
	default:
		return fmt.Errorf("DATAPLANE_STORAGE must be postgres or memory")
	}
	switch c.BusBackend {
	case "mqtt":
		if c.MQTTBrokerURL == "" {
			return fmt.Errorf("DATAPLANE_MQTT_URL is required with DATAPLANE_BUS=mqtt (for example mqtts://emqx:8883)")
		}
		if c.MQTTPassword == "" {
			return fmt.Errorf("MQTT_PLATFORM_PASSWORD is required with DATAPLANE_BUS=mqtt")
		}
	case "memory":
	default:
		return fmt.Errorf("DATAPLANE_BUS must be mqtt or memory")
	}
	if c.MQTTJWTSecret != "" && len(c.MQTTJWTSecret) < 32 {
		return fmt.Errorf("DATAPLANE_MQTT_JWT_SECRET must be at least 32 characters")
	}
	if c.MaxBatch < 1 || c.QueueSize < c.MaxBatch {
		return fmt.Errorf("DATAPLANE_QUEUE_SIZE must be at least DATAPLANE_MAX_BATCH")
	}
	return nil
}
