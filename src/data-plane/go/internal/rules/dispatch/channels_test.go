package dispatch

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"meteorcloud/dataplane/internal/rules/dsl"
	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

// smtpServer is a minimal mail server; rcptCode answers RCPT TO.
func smtpServer(t *testing.T, rcptCode string) (addr string, got chan string) {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close() })
	got = make(chan string, 4)
	go func() {
		for {
			c, err := l.Accept()
			if err != nil {
				return
			}
			go func() {
				defer c.Close()
				r := bufio.NewReader(c)
				var log strings.Builder
				fmt.Fprint(c, "220 test\r\n")
				data := false
				for {
					line, err := r.ReadString('\n')
					if err != nil {
						return
					}
					log.WriteString(line)
					switch cmd := strings.ToUpper(strings.TrimSpace(line)); {
					case data && cmd == ".":
						data = false
						got <- log.String()
						fmt.Fprint(c, "250 queued\r\n")
					case data:
					case cmd == "DATA":
						data = true
						fmt.Fprint(c, "354 go\r\n")
					case strings.HasPrefix(cmd, "RCPT"):
						fmt.Fprint(c, rcptCode+"\r\n")
					case cmd == "QUIT":
						fmt.Fprint(c, "221 bye\r\n")
						return
					default:
						fmt.Fprint(c, "250 ok\r\n")
					}
				}
			}()
		}
	}()
	return l.Addr().String(), got
}

func samplePayload(name string) *engine.ActionPayload {
	return &engine.ActionPayload{RuleName: name, OrganizationID: "org-1", DeviceID: "dev-1", Severity: "critical",
		Transition: engine.Transition{Kind: engine.TransitionFiring, At: t0, Values: map[string]float64{"cpu_percent": 97.5}}}
}

func TestEmailDelivery(t *testing.T) {
	addr, got := smtpServer(t, "250 ok")
	s := &WebhookSender{Email: &SMTP{Addr: addr, From: "Alerts <alerts@example.com>", TLS: "none"}, ConsoleURL: "https://c.example/"}
	d := &Delivery{Kind: "email", DeviceName: "pump-7", OrganizationName: "Acme"}
	d.Config.Recipients = []string{"a@example.com", "b@example.com"}
	// A rule name cannot add headers or recipients.
	if _, err := s.Deliver(context.Background(), d, state.OutboxItem{Key: "k1"}, samplePayload("Hot\r\nBcc: evil@example.com")); err != nil {
		t.Fatal(err)
	}
	msg := <-got
	for _, want := range []string{"RCPT TO:<a@example.com>", "RCPT TO:<b@example.com>", "To: a@example.com, b@example.com\r\n",
		"Subject: [ALERT] CRITICAL: Hot Bcc: evil@example.com on pump-7\r\n", "Device: pump-7", "CPU: 97.5%",
		"https://c.example/organizations/org-1/alerts", "Message-ID: <"} {
		if !strings.Contains(msg, want) {
			t.Errorf("missing %q in:\n%s", want, msg)
		}
	}
	if strings.Contains(msg, "\r\nBcc:") {
		t.Fatal("header injection")
	}
}

func TestEmailErrors(t *testing.T) {
	ctx := context.Background()
	d := &Delivery{Kind: "email"}
	d.Config.Recipients = []string{"a@example.com"}
	if _, err := (&WebhookSender{}).Deliver(ctx, d, state.OutboxItem{}, samplePayload("x")); !IsPermanent(err) {
		t.Fatalf("unconfigured email is permanent: %v", err)
	}
	for code, permanent := range map[string]bool{"550 no such user": true, "451 try later": false} {
		addr, _ := smtpServer(t, code)
		err := (&SMTP{Addr: addr, From: "a@example.com", TLS: "none"}).Send(ctx, d.Config.Recipients, "s", "b", "k")
		if err == nil || IsPermanent(err) != permanent {
			t.Errorf("%s: permanent=%v err=%v", code, IsPermanent(err), err)
		}
	}
	// Never falls back to plain text when STARTTLS is expected.
	addr, _ := smtpServer(t, "250 ok")
	if err := (&SMTP{Addr: addr, From: "a@example.com"}).Send(ctx, d.Config.Recipients, "s", "b", "k"); err == nil || !strings.Contains(err.Error(), "STARTTLS") {
		t.Fatalf("starttls required: %v", err)
	}
	for _, bad := range []SMTP{{Addr: "x", From: "a@b.c"}, {Addr: "x:25", From: "nope"}, {Addr: "x:25", From: "a@b.c", TLS: "maybe"},
		{Addr: "x:25", From: "a@b.c", TLS: "none", Username: "u"}} {
		if bad.Validate() == nil {
			t.Errorf("accepted %+v", bad)
		}
	}
}

const botToken = "123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQr"

func TestTelegramDelivery(t *testing.T) {
	var path string
	var body map[string]any
	status := 200
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		raw, _ := io.ReadAll(r.Body)
		json.Unmarshal(raw, &body)
		w.WriteHeader(status)
		w.Write([]byte(`{"ok":false,"description":"chat not found"}`))
	}))
	s := &WebhookSender{Telegram: &Telegram{API: srv.URL, Client: srv.Client()}}
	d := &Delivery{Kind: "telegram", Secret: botToken, DeviceName: "pump-7"}
	d.Config.ChatID = "-100200"
	ctx := context.Background()
	if _, err := s.Deliver(ctx, d, state.OutboxItem{}, samplePayload("Hot")); err != nil {
		t.Fatal(err)
	}
	if path != "/bot"+botToken+"/sendMessage" || body["chat_id"] != "-100200" ||
		!strings.Contains(body["text"].(string), "[ALERT] CRITICAL: Hot on pump-7") || body["parse_mode"] != nil {
		t.Fatalf("%s %v", path, body)
	}
	status = 400
	if _, err := s.Deliver(ctx, d, state.OutboxItem{}, samplePayload("Hot")); !IsPermanent(err) || !strings.Contains(err.Error(), "chat not found") {
		t.Fatalf("400: %v", err)
	}
	status = 429
	if _, err := s.Deliver(ctx, d, state.OutboxItem{}, samplePayload("Hot")); err == nil || IsPermanent(err) {
		t.Fatalf("429 is retried: %v", err)
	}
	srv.Close()
	_, err := s.Deliver(ctx, d, state.OutboxItem{}, samplePayload("Hot"))
	if err == nil || IsPermanent(err) || strings.Contains(err.Error(), botToken) {
		t.Fatalf("an unreachable API is retried and never prints the token: %v", err)
	}
	d.Secret = "../../evil"
	if _, err := s.Deliver(ctx, d, state.OutboxItem{}, samplePayload("Hot")); !IsPermanent(err) {
		t.Fatalf("bad token refused: %v", err)
	}
}

type targetSender struct{ sent []string }

func (s *targetSender) Send(_ context.Context, it state.OutboxItem, _ *engine.ActionPayload) (Outcome, error) {
	if it.Target == "broken" {
		return "", errors.New("down")
	}
	s.sent = append(s.sent, it.Target+":"+it.Transition)
	return Delivered, nil
}

// One unreachable channel must not hold back the rule's other channels.
func TestChannelsOfOneRuleAreIndependent(t *testing.T) {
	store := state.NewMemory()
	s := &targetSender{}
	clock := engine.NewFakeClock(t0)
	d := New(Config{Workers: 1}, store, map[string]Sender{dsl.ActionNotification: s}, clock, nil, nil)
	var items []state.OutboxItem
	for i, tr := range []string{"firing", "resolved"} {
		for _, target := range []string{"broken", "ok"} {
			items = append(items, state.OutboxItem{Key: fmt.Sprint(target, i), RuleID: "r", DeviceID: "d", Transition: tr,
				ActionType: dsl.ActionNotification, Target: target, Payload: json.RawMessage(`{}`), NextAttempt: t0, CreatedAt: t0})
		}
	}
	if errs := store.CommitMany(context.Background(), []*state.Batch{{Outbox: items}}); errs[0] != nil {
		t.Fatal(errs[0])
	}
	for i := 0; i < 6; i++ {
		d.Pass(context.Background())
	}
	if fmt.Sprint(s.sent) != "[ok:firing ok:resolved]" {
		t.Fatalf("sent %v", s.sent)
	}
	clock.Advance(time.Second)
}
