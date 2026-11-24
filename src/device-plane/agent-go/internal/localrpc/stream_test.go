package localrpc

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"strings"
	"testing"
	"time"
)

func streamServer(t *testing.T, stream func(ctx context.Context, w io.Writer) error) string {
	t.Helper()
	return serve(t, &Server{
		Handlers: map[string]Handler{"logs": {Timeout: time.Hour, Stream: func(ctx context.Context, _ Peer, _ json.RawMessage, w io.Writer) error {
			return stream(ctx, w)
		}}},
		Authorize: func(Peer, string) error { return nil },
		Log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
}

func TestStreamDeliversLinesThenTheOutcome(t *testing.T) {
	path := streamServer(t, func(_ context.Context, w io.Writer) error {
		_, _ = io.WriteString(w, "one\ntw")
		_, _ = io.WriteString(w, "o\n"+strings.Repeat("x", maxStreamLine+10)+"\nlast")
		return nil
	})
	var got []string
	err := CallStream(context.Background(), path, "logs", nil, func(l string) error { got = append(got, l); return nil })
	if err != nil || len(got) != 4 || got[0] != "one" || got[1] != "two" || len(got[2]) != maxStreamLine || got[3] != "last" {
		t.Fatalf("lines %d %q, err %v", len(got), got[:min(len(got), 2)], err)
	}
}

func TestStreamReportsTheErrorAfterTheLines(t *testing.T) {
	path := streamServer(t, func(_ context.Context, w io.Writer) error {
		_, _ = io.WriteString(w, "partial\n")
		return Errorf(CodeBadRequest, "no such unit")
	})
	var got []string
	err := CallStream(context.Background(), path, "logs", nil, func(l string) error { got = append(got, l); return nil })
	if CodeOf(err) != CodeBadRequest || len(got) != 1 {
		t.Fatalf("lines %q, err %v", got, err)
	}
}

func TestStreamEndsWhenTheCallerHangsUp(t *testing.T) {
	ended := make(chan error, 1)
	path := streamServer(t, func(ctx context.Context, w io.Writer) error {
		_, _ = io.WriteString(w, "first\n")
		<-ctx.Done() // a quiet followed log
		ended <- ctx.Err()
		return nil
	})
	ctx, cancel := context.WithCancel(context.Background())
	err := CallStream(ctx, path, "logs", nil, func(string) error { cancel(); return nil })
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err %v", err)
	}
	select {
	case <-ended:
	case <-time.After(5 * time.Second):
		t.Fatal("the handler kept running after the caller hung up")
	}
}

func TestStreamIsAuthorizedLikeACall(t *testing.T) {
	path := serve(t, &Server{
		Handlers: map[string]Handler{"logs": {Stream: func(context.Context, Peer, json.RawMessage, io.Writer) error {
			t.Error("an unauthorized stream ran")
			return nil
		}}},
		Authorize: func(Peer, string) error { return Errorf(CodeDenied, "no") },
		Log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	if err := CallStream(context.Background(), path, "logs", nil, func(string) error { return nil }); CodeOf(err) != CodeDenied {
		t.Fatalf("err %v", err)
	}
}
