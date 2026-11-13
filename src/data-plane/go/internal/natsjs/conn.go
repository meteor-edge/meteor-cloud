package natsjs

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

// Connect opens a NATS connection that keeps retrying in the background: the
// first connection attempt may fail (NATS not up yet) and later disconnects are
// reconnected forever unless MaxReconnects says otherwise. Connection state is
// reported through metrics and the log.
func Connect(cfg ConnConfig, m *Metrics, log *slog.Logger) (*nats.Conn, error) {
	opts := []nats.Option{
		nats.Name(cfg.Name),
		nats.Timeout(cfg.ConnectWait),
		nats.RetryOnFailedConnect(true),
		nats.MaxReconnects(cfg.MaxReconnects),
		nats.ReconnectWait(cfg.ReconnectWait),
		nats.ReconnectJitter(500*time.Millisecond, 2*time.Second),
		nats.ReconnectBufSize(cfg.ReconnectBufBytes),
		nats.ConnectHandler(func(nc *nats.Conn) {
			m.Connected.Set(1)
			log.Info("NATS connected", "server", nc.ConnectedUrlRedacted())
		}),
		nats.DisconnectErrHandler(func(_ *nats.Conn, err error) {
			m.Connected.Set(0)
			m.Disconnects.Inc()
			if err != nil {
				log.Warn("NATS disconnected", "error", err.Error())
			}
		}),
		nats.ReconnectHandler(func(nc *nats.Conn) {
			m.Connected.Set(1)
			m.Reconnects.Inc()
			log.Info("NATS reconnected", "server", nc.ConnectedUrlRedacted())
		}),
		nats.ClosedHandler(func(*nats.Conn) { m.Connected.Set(0) }),
		nats.ErrorHandler(func(_ *nats.Conn, sub *nats.Subscription, err error) {
			m.AsyncErrors.Inc()
			subject := ""
			if sub != nil {
				subject = sub.Subject
			}
			log.Warn("NATS asynchronous error", "subject", subject, "error", err.Error())
		}),
	}
	if cfg.InboxPrefix != "" {
		opts = append(opts, nats.CustomInboxPrefix(cfg.InboxPrefix))
	}
	if cfg.CredsFile != "" {
		opts = append(opts, nats.UserCredentials(cfg.CredsFile))
	} else if cfg.User != "" {
		opts = append(opts, nats.UserInfo(cfg.User, cfg.Password))
	}
	if cfg.TLSCAFile != "" {
		opts = append(opts, nats.RootCAs(cfg.TLSCAFile))
	}
	if cfg.TLSCertFile != "" || cfg.TLSKeyFile != "" {
		if cfg.TLSCertFile == "" || cfg.TLSKeyFile == "" {
			return nil, errors.New("NATS client TLS needs both a certificate and a key file")
		}
		opts = append(opts, nats.ClientCert(cfg.TLSCertFile, cfg.TLSKeyFile))
	}
	nc, err := nats.Connect(strings.TrimSpace(cfg.URLs), opts...)
	if err != nil {
		return nil, fmt.Errorf("nats: %w", err)
	}
	return nc, nil
}

// Retry calls fn until it succeeds or ctx ends, with capped exponential backoff.
// Start-up steps (stream, consumer) use it so a service can start before NATS.
func Retry(ctx context.Context, log *slog.Logger, what string, fn func(context.Context) error) error {
	backoff := 250 * time.Millisecond
	for {
		actx, cancel := context.WithTimeout(ctx, 10*time.Second)
		err := fn(actx)
		cancel()
		if err == nil {
			return nil
		}
		if ctx.Err() != nil {
			return fmt.Errorf("%s: %w", what, err)
		}
		log.Warn("retrying", "step", what, "in", backoff.String(), "error", err.Error())
		select {
		case <-ctx.Done():
			return fmt.Errorf("%s: %w", what, ctx.Err())
		case <-time.After(backoff):
		}
		if backoff < 10*time.Second {
			backoff *= 2
		}
	}
}

// EnsureStream creates the stream or updates it to cfg. With Manage off it only
// looks the stream up, so a service never changes operator-managed streams.
func EnsureStream(ctx context.Context, js jetstream.JetStream, cfg StreamConfig) (jetstream.Stream, error) {
	if !cfg.Manage {
		return js.Stream(ctx, cfg.Name)
	}
	return js.CreateOrUpdateStream(ctx, jetstream.StreamConfig{
		Name:        cfg.Name,
		Description: "MeteorCloud device telemetry, status and events (versioned subjects)",
		Subjects:    cfg.Subjects,
		Retention:   cfg.Retention,
		MaxBytes:    cfg.MaxBytes,
		MaxAge:      cfg.MaxAge,
		MaxMsgSize:  cfg.MaxMsgSize,
		Storage:     cfg.Storage,
		Replicas:    cfg.Replicas,
		Duplicates:  cfg.DuplicateWindow,
		Discard:     cfg.Discard,
		MaxMsgs:     -1,
	})
}

// EnsureConsumer creates or updates a durable pull consumer with explicit acks.
// Each service owns its durable, so its progress is independent of every other.
func EnsureConsumer(ctx context.Context, js jetstream.JetStream, cfg ConsumerConfig) (jetstream.Consumer, error) {
	cc := jetstream.ConsumerConfig{
		Durable:       cfg.Durable,
		AckPolicy:     jetstream.AckExplicitPolicy,
		AckWait:       cfg.AckWait,
		MaxDeliver:    cfg.MaxDeliver,
		BackOff:       cfg.BackOff,
		MaxAckPending: cfg.MaxAckPending,
		DeliverPolicy: cfg.DeliverPolicy,
		// Larger pulls than this are refused, which keeps one replica from hoarding.
		MaxRequestBatch: cfg.FetchBatch,
	}
	if len(cfg.FilterSubjects) == 1 {
		cc.FilterSubject = cfg.FilterSubjects[0]
	} else {
		cc.FilterSubjects = cfg.FilterSubjects
	}
	return js.CreateOrUpdateConsumer(ctx, cfg.Stream, cc)
}

// WatchConsumer refreshes the pending-message gauges of a consumer until ctx ends.
func WatchConsumer(ctx context.Context, cons jetstream.Consumer, name string, every time.Duration, m *Metrics) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		ictx, cancel := context.WithTimeout(ctx, 5*time.Second)
		info, err := cons.Info(ictx)
		cancel()
		if err == nil {
			m.Pending.WithLabelValues(name).Set(float64(info.NumPending))
			m.AckPending.WithLabelValues(name).Set(float64(info.NumAckPending))
			m.Redelivered.WithLabelValues(name).Set(float64(info.NumRedelivered))
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}
