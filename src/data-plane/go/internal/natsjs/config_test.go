package natsjs

import (
	"testing"
	"time"

	"github.com/nats-io/nats.go/jetstream"
)

func TestStreamConfigFromEnv(t *testing.T) {
	t.Setenv("NATS_STREAM_REPLICAS", "3")
	t.Setenv("NATS_STREAM_MAX_BYTES", "50GB")
	t.Setenv("NATS_STREAM_MAX_AGE", "72h")
	t.Setenv("NATS_STREAM_STORAGE", "memory")
	t.Setenv("NATS_STREAM_RETENTION", "interest")
	e := &Env{}
	c := LoadStreamConfig(e)
	if e.Err != nil {
		t.Fatal(e.Err)
	}
	if c.Replicas != 3 || c.MaxBytes != 50<<30 || c.MaxAge != 72*time.Hour || c.Storage != jetstream.MemoryStorage ||
		c.Retention != jetstream.InterestPolicy || c.Name != DefaultStream || len(c.Subjects) != 3 {
		t.Fatalf("%+v", c)
	}
	t.Setenv("NATS_STREAM_REPLICAS", "0")
	e = &Env{}
	LoadStreamConfig(e)
	if e.Err == nil {
		t.Fatal("replicas 0 accepted")
	}
}

func TestConsumerConfigFromEnv(t *testing.T) {
	t.Setenv("RULES_CONSUMER_DURABLE", "rules-eu")
	t.Setenv("RULES_CONSUMER_BACKOFF", "1s,10s")
	t.Setenv("RULES_CONSUMER_FILTER_SUBJECTS", "telemetry.v1.>")
	e := &Env{}
	c := LoadConsumerConfig(e, "RULES_CONSUMER", ConsumerConfig{Durable: "rule-processor", FetchBatch: 10, MaxAckPending: 100})
	if e.Err != nil || c.Durable != "rules-eu" || len(c.BackOff) != 2 || c.FilterSubjects[0] != "telemetry.v1.>" {
		t.Fatalf("%+v %v", c, e.Err)
	}
	t.Setenv("RULES_CONSUMER_FETCH_BATCH", "500")
	e = &Env{}
	LoadConsumerConfig(e, "RULES_CONSUMER", ConsumerConfig{Durable: "x", FetchBatch: 10, MaxAckPending: 100})
	if e.Err == nil {
		t.Fatal("a fetch batch above max ack pending must be refused")
	}
}

func TestRetryDelayAndSubjectRoot(t *testing.T) {
	s := []time.Duration{time.Second, 5 * time.Second}
	if RetryDelay(1, s) != time.Second || RetryDelay(2, s) != 5*time.Second || RetryDelay(9, s) != 5*time.Second {
		t.Fatal("retry delay")
	}
	if subjectRoot("telemetry.v1.o.d") != "telemetry.v1" || subjectRoot("device.status.v1.o.d") != "device.status.v1" {
		t.Fatal("subject root")
	}
}
