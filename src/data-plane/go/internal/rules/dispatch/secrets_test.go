package dispatch

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"meteorcloud/dataplane/internal/rules/state"
)

// Errors are logged and stored with the dead letter: neither the URL (a Slack URL is a
// credential) nor what the receiver answered may be in them.
func TestWebhookErrorsCarryNoSecrets(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "internal-answer", http.StatusForbidden)
	}))
	url := srv.URL + "/services/TOPSECRET"
	send := func(client *http.Client) error {
		_, err := (&WebhookSender{Client: client}).Deliver(context.Background(), &Delivery{Kind: "slack", URL: url}, state.OutboxItem{Key: "k"}, samplePayload("x"))
		if err == nil || strings.Contains(err.Error(), "TOPSECRET") || strings.Contains(err.Error(), "internal-answer") {
			t.Fatalf("error %v", err)
		}
		return err
	}
	if err := send(NewWebhookClient(true)); !IsPermanent(err) || !strings.Contains(err.Error(), "403") {
		t.Fatal(err)
	}
	if err := send(NewWebhookClient(false)); !IsPermanent(err) || !strings.Contains(err.Error(), "is not public") {
		t.Fatal(err)
	}
	srv.Close()
	if err := send(NewWebhookClient(true)); IsPermanent(err) {
		t.Fatal(err)
	}
	if err := (&SMTP{Addr: "127.0.0.1:1", From: "a@example.com"}).Send(context.Background(), []string{"a@example.com\r\nBcc: b@example.com"}, "s", "b", "k"); !IsPermanent(err) {
		t.Fatal(errors.New("a recipient cannot add headers"), err)
	}
}
