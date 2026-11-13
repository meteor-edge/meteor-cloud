// Package natsjs connects data-plane services to NATS JetStream: connection,
// stream and consumer configuration from the environment, persistent publishing
// and a bounded pull loop for durable consumers.
//
// It is deliberately thin. Callers get the official nats.go and jetstream types
// back (nats.Conn, jetstream.Stream, jetstream.Consumer, jetstream.Msg), so every
// JetStream capability stays reachable; this package only removes boilerplate
// that every service would otherwise repeat.
package natsjs

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/nats-io/nats.go/jetstream"
)

// ConnConfig is how a service reaches NATS. Each service has its own user (or
// credentials file), so the server can restrict it to the subjects it needs.
type ConnConfig struct {
	URLs      string // comma separated, e.g. nats://nats-1:4222,nats://nats-2:4222
	Name      string // connection name shown in NATS monitoring
	User      string
	Password  string
	CredsFile string // NATS decentralized-auth credentials (JWT + nkey), instead of user/password
	// InboxPrefix replaces "_INBOX" for replies (publish acks, pulled messages), so
	// the server can let each service subscribe to its own inboxes only.
	InboxPrefix   string
	TLSCAFile     string
	TLSCertFile   string // client certificate (mutual TLS), optional
	TLSKeyFile    string
	ConnectWait   time.Duration // per attempt
	ReconnectWait time.Duration
	// MaxReconnects < 0 retries forever (the default): a data-plane service must
	// outlive a NATS restart or a cluster leader change.
	MaxReconnects int
	// ReconnectBufBytes buffers core publishes while reconnecting. JetStream
	// publishes wait for an acknowledgement, so nothing is lost when it is full.
	ReconnectBufBytes int
}

// StreamConfig is the JetStream stream that captures device subjects.
type StreamConfig struct {
	Name     string
	Subjects []string
	// Retention: "limits" (default; every consumer reads independently and messages
	// age out by MaxAge/MaxBytes), "interest" (removed once every consumer acked) or
	// "workqueue" (one consumer per subject; not suitable for several consumers).
	Retention       jetstream.RetentionPolicy
	MaxBytes        int64         // -1 unlimited
	MaxAge          time.Duration // 0 unlimited
	MaxMsgSize      int32         // -1 unlimited
	Storage         jetstream.StorageType
	Replicas        int           // 1 for a single server, 3 for a three-node cluster
	DuplicateWindow time.Duration // Nats-Msg-Id de-duplication window
	Discard         jetstream.DiscardPolicy
	// Manage: create or update the stream at start-up. Turn off where operators
	// manage streams themselves (nats CLI, NACK, Terraform) and services only bind.
	Manage bool
}

// ConsumerConfig is one durable pull consumer.
type ConsumerConfig struct {
	Stream         string
	Durable        string
	FilterSubjects []string
	AckWait        time.Duration
	// MaxDeliver < 0 redelivers forever. A storage consumer should never give up
	// on a message just because storage is down; the stream's limits bound it.
	MaxDeliver int
	// BackOff is the redelivery delay after an AckWait expiry, per attempt. When set,
	// its first entry replaces AckWait as the first redelivery delay.
	BackOff       []time.Duration
	MaxAckPending int // bound on unacknowledged messages across all replicas
	FetchBatch    int // messages per pull
	FetchMaxWait  time.Duration
	DeliverPolicy jetstream.DeliverPolicy
}

func env(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

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

// Env reads typed values and remembers the first error, so a loader reads like a
// list of settings.
type Env struct{ Err error }

func (e *Env) fail(key string, err error) {
	if e.Err == nil {
		e.Err = fmt.Errorf("%s: %w", key, err)
	}
}

func (e *Env) String(key, def string) string { return env(key, def) }

func (e *Env) Int(key string, def int) int {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		e.fail(key, err)
	}
	return n
}

func (e *Env) Int64(key string, def int64) int64 {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	n, err := parseBytes(v)
	if err != nil {
		e.fail(key, err)
	}
	return n
}

func (e *Env) Duration(key string, def time.Duration) time.Duration {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	d, err := time.ParseDuration(v)
	if err != nil {
		e.fail(key, err)
	}
	return d
}

func (e *Env) Durations(key string, def []time.Duration) []time.Duration {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	var out []time.Duration
	for _, part := range strings.Split(v, ",") {
		d, err := time.ParseDuration(strings.TrimSpace(part))
		if err != nil {
			e.fail(key, err)
			return def
		}
		out = append(out, d)
	}
	return out
}

func (e *Env) List(key string, def []string) []string {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	var out []string
	for _, part := range strings.Split(v, ",") {
		if p := strings.TrimSpace(part); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func (e *Env) Secret(key string) string {
	v, err := secret(key)
	if err != nil {
		e.fail(key, err)
	}
	return v
}

// parseBytes accepts plain integers and K/M/G/T suffixes (powers of 1024), and -1.
func parseBytes(v string) (int64, error) {
	v = strings.TrimSpace(strings.ToUpper(v))
	mult := int64(1)
	for _, s := range []struct {
		suffix string
		mult   int64
	}{{"TB", 1 << 40}, {"GB", 1 << 30}, {"MB", 1 << 20}, {"KB", 1 << 10}, {"T", 1 << 40}, {"G", 1 << 30}, {"M", 1 << 20}, {"K", 1 << 10}} {
		if strings.HasSuffix(v, s.suffix) {
			mult, v = s.mult, strings.TrimSuffix(v, s.suffix)
			break
		}
	}
	n, err := strconv.ParseInt(strings.TrimSpace(v), 10, 64)
	if err != nil {
		return 0, err
	}
	return n * mult, nil
}

// LoadConnConfig reads NATS_* connection settings. prefix selects per-service
// credentials, e.g. "NATS_INGEST" reads NATS_INGEST_USER and NATS_INGEST_PASSWORD.
func LoadConnConfig(e *Env, name, prefix string) ConnConfig {
	return ConnConfig{
		URLs:              e.String("NATS_URL", "nats://127.0.0.1:4222"),
		Name:              e.String("NATS_CONNECTION_NAME", name),
		User:              e.String(prefix+"_USER", ""),
		Password:          e.Secret(prefix + "_PASSWORD"),
		CredsFile:         e.String(prefix+"_CREDS_FILE", ""),
		InboxPrefix:       e.String(prefix+"_INBOX_PREFIX", "_INBOX_"+name),
		TLSCAFile:         e.String("NATS_TLS_CA_FILE", ""),
		TLSCertFile:       e.String(prefix+"_TLS_CERT_FILE", ""),
		TLSKeyFile:        e.String(prefix+"_TLS_KEY_FILE", ""),
		ConnectWait:       e.Duration("NATS_CONNECT_TIMEOUT", 5*time.Second),
		ReconnectWait:     e.Duration("NATS_RECONNECT_WAIT", 2*time.Second),
		MaxReconnects:     e.Int("NATS_MAX_RECONNECTS", -1),
		ReconnectBufBytes: e.Int("NATS_RECONNECT_BUFFER_BYTES", 8<<20),
	}
}

// Stream defaults. Subjects are versioned; a v2 schema gets new subjects and can
// live in the same stream while consumers migrate.
const (
	DefaultStream = "METEOR_DEVICE"
)

// DefaultSubjects are the subjects the device stream captures.
var DefaultSubjects = []string{"telemetry.v1.>", "device.status.v1.>", "device.events.v1.>"}

// LoadStreamConfig reads NATS_STREAM_* settings. Defaults suit one development
// server; production sets NATS_STREAM_REPLICAS=3 on a three-node cluster.
func LoadStreamConfig(e *Env) StreamConfig {
	c := StreamConfig{
		Name:            e.String("NATS_STREAM_NAME", DefaultStream),
		Subjects:        e.List("NATS_STREAM_SUBJECTS", DefaultSubjects),
		MaxBytes:        e.Int64("NATS_STREAM_MAX_BYTES", 10<<30),
		MaxAge:          e.Duration("NATS_STREAM_MAX_AGE", 7*24*time.Hour),
		MaxMsgSize:      int32(e.Int64("NATS_STREAM_MAX_MSG_SIZE", 1<<20)),
		Replicas:        e.Int("NATS_STREAM_REPLICAS", 1),
		DuplicateWindow: e.Duration("NATS_STREAM_DUPLICATE_WINDOW", 2*time.Minute),
		Manage:          e.String("NATS_STREAM_MANAGE", "true") == "true",
	}
	switch r := e.String("NATS_STREAM_RETENTION", "limits"); r {
	case "limits":
		c.Retention = jetstream.LimitsPolicy
	case "interest":
		c.Retention = jetstream.InterestPolicy
	case "workqueue":
		c.Retention = jetstream.WorkQueuePolicy
	default:
		e.fail("NATS_STREAM_RETENTION", fmt.Errorf("%q is not limits, interest or workqueue", r))
	}
	switch s := e.String("NATS_STREAM_STORAGE", "file"); s {
	case "file":
		c.Storage = jetstream.FileStorage
	case "memory":
		c.Storage = jetstream.MemoryStorage
	default:
		e.fail("NATS_STREAM_STORAGE", fmt.Errorf("%q is not file or memory", s))
	}
	switch d := e.String("NATS_STREAM_DISCARD", "old"); d {
	case "old":
		c.Discard = jetstream.DiscardOld
	case "new":
		// Publishers get an error when the stream is full instead of old data being
		// dropped: ingestion then stops acknowledging MQTT, which is backpressure.
		c.Discard = jetstream.DiscardNew
	default:
		e.fail("NATS_STREAM_DISCARD", fmt.Errorf("%q is not old or new", d))
	}
	if c.Replicas < 1 || c.Replicas > 5 {
		e.fail("NATS_STREAM_REPLICAS", fmt.Errorf("must be 1-5, got %d", c.Replicas))
	}
	return c
}

// LoadConsumerConfig reads <prefix>_* consumer settings, e.g. RULES_CONSUMER_DURABLE.
func LoadConsumerConfig(e *Env, prefix string, def ConsumerConfig) ConsumerConfig {
	c := ConsumerConfig{
		Stream:         e.String("NATS_STREAM_NAME", DefaultStream),
		Durable:        e.String(prefix+"_DURABLE", def.Durable),
		FilterSubjects: e.List(prefix+"_FILTER_SUBJECTS", def.FilterSubjects),
		AckWait:        e.Duration(prefix+"_ACK_WAIT", def.AckWait),
		MaxDeliver:     e.Int(prefix+"_MAX_DELIVER", def.MaxDeliver),
		BackOff:        e.Durations(prefix+"_BACKOFF", def.BackOff),
		MaxAckPending:  e.Int(prefix+"_MAX_ACK_PENDING", def.MaxAckPending),
		FetchBatch:     e.Int(prefix+"_FETCH_BATCH", def.FetchBatch),
		FetchMaxWait:   e.Duration(prefix+"_FETCH_MAX_WAIT", def.FetchMaxWait),
	}
	switch d := e.String(prefix+"_DELIVER_POLICY", "all"); d {
	case "all":
		c.DeliverPolicy = jetstream.DeliverAllPolicy
	case "new":
		c.DeliverPolicy = jetstream.DeliverNewPolicy
	default:
		e.fail(prefix+"_DELIVER_POLICY", fmt.Errorf("%q is not all or new", d))
	}
	if c.Durable == "" {
		e.fail(prefix+"_DURABLE", fmt.Errorf("must not be empty"))
	}
	if c.FetchBatch < 1 {
		e.fail(prefix+"_FETCH_BATCH", fmt.Errorf("must be at least 1"))
	}
	if c.MaxAckPending > 0 && c.FetchBatch > c.MaxAckPending {
		e.fail(prefix+"_FETCH_BATCH", fmt.Errorf("must not exceed %s_MAX_ACK_PENDING", prefix))
	}
	return c
}
