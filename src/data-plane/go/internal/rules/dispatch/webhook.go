package dispatch

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"strconv"
	"strings"
	"syscall"
	"time"

	"meteorcloud/dataplane/internal/rules/engine"
	"meteorcloud/dataplane/internal/rules/state"
)

const (
	defaultWebhookTimeout = 10 * time.Second
	maxWebhookTimeout     = 60 * time.Second
	// SignatureHeader carries "t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">".
	SignatureHeader = "X-MeteorCloud-Signature"
)

// Delivery is where one webhook or notification goes, as the control plane answers it.
type Delivery struct {
	URL      string `json:"url"`
	Kind     string `json:"kind"` // webhook | slack | email | telegram
	Secret   string `json:"secret"`
	Enabled  bool   `json:"enabled"`
	Silenced bool   `json:"silenced"`
	// Config holds what a kind needs besides URL and secret.
	Config struct {
		Recipients []string `json:"recipients"` // email
		ChatID     string   `json:"chat_id"`    // telegram
	} `json:"config"`
	DeviceName       string `json:"device_name"`
	OrganizationName string `json:"organization_name"`
	OrganizationID   string `json:"organization_id"`
	Link             string `json:"-"` // console page, set by the sender
	// Group lists the devices of a message that covers several; empty for one device.
	Group []string `json:"-"`
}

// Resolver looks up an integration for one action.
type Resolver interface {
	Resolve(ctx context.Context, item state.OutboxItem, p *engine.ActionPayload) (*Delivery, error)
}

// ControlPlaneResolver asks POST /internal/rules/deliveries, which checks that the
// integration belongs to the action's organization and whether a silence holds it.
type ControlPlaneResolver struct{ CP *ControlPlane }

func (r *ControlPlaneResolver) Resolve(ctx context.Context, item state.OutboxItem, p *engine.ActionPayload) (*Delivery, error) {
	var d Delivery
	err := r.CP.Post(ctx, "/internal/rules/deliveries", map[string]any{
		"integration_id": p.Action.IntegrationID, "organization_id": item.OrgID, "rule_id": item.RuleID,
		"device_id": item.DeviceID, "at": p.Transition.At,
	}, &d)
	return &d, err
}

// TestResolver looks up an integration for a test message.
type TestResolver interface {
	ResolveTest(ctx context.Context, organizationID, integrationID string) (*Delivery, error)
}

// ResolveTest asks POST /internal/rules/test-deliveries.
func (r *ControlPlaneResolver) ResolveTest(ctx context.Context, organizationID, integrationID string) (*Delivery, error) {
	var d Delivery
	err := r.CP.Post(ctx, "/internal/rules/test-deliveries", map[string]any{
		"integration_id": integrationID, "organization_id": organizationID}, &d)
	return &d, err
}

// WebhookSender posts webhooks and notifications. Its HTTP client refuses private,
// loopback and link-local addresses (checked on the resolved address at connect
// time, so DNS tricks do not help) unless AllowPrivate is set, and never follows
// redirects.
type WebhookSender struct {
	Resolver Resolver
	Client   *http.Client
	Now      func() time.Time
	Email    *SMTP     // nil: email is not configured on this platform
	Telegram *Telegram // nil: the public Bot API with the guarded client
	// ConsoleURL, when set, adds a link to the alerts page to messages.
	ConsoleURL string
}

// NewWebhookClient returns the guarded HTTP client used for outgoing webhooks.
func NewWebhookClient(allowPrivate bool) *http.Client {
	dialer := &net.Dialer{Timeout: 10 * time.Second, Control: func(_, address string, _ syscall.RawConn) error {
		if allowPrivate {
			return nil
		}
		host, _, err := net.SplitHostPort(address)
		if err != nil {
			return err
		}
		ip, err := netip.ParseAddr(host)
		if err != nil {
			return err
		}
		if blocked(ip) {
			return fmt.Errorf("webhook address %s is not public (set RULES_WEBHOOK_ALLOW_PRIVATE=true to allow it)", ip)
		}
		return nil
	}}
	return &http.Client{
		Transport: &http.Transport{DialContext: dialer.DialContext, Proxy: nil, MaxIdleConnsPerHost: 4,
			IdleConnTimeout: 90 * time.Second, TLSHandshakeTimeout: 10 * time.Second, ResponseHeaderTimeout: maxWebhookTimeout},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

// notPublic is what the netip predicates below do not cover: ranges that are not on the
// internet, and IPv6 ranges that carry an IPv4 address a gateway would unwrap.
var notPublic = func() (out []netip.Prefix) {
	for _, p := range []string{
		"0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24",
		"203.0.113.0/24", "240.0.0.0/4",
		"::/96", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "2001::/32", "2001:db8::/32", "2002::/16", "fec0::/10",
	} {
		out = append(out, netip.MustParsePrefix(p))
	}
	return out
}()

func blocked(ip netip.Addr) bool {
	ip = ip.Unmap().WithZone("")
	for _, p := range notPublic {
		if p.Contains(ip) {
			return true
		}
	}
	return !ip.IsValid() || ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() ||
		ip.IsInterfaceLocalMulticast() || ip.IsMulticast() || ip.IsUnspecified()
}

func (s *WebhookSender) Send(ctx context.Context, item state.OutboxItem, p *engine.ActionPayload) (Outcome, error) {
	d, err := s.Resolver.Resolve(ctx, item, p)
	if err != nil {
		return "", err
	}
	if !d.Enabled || d.Silenced {
		return Skipped, nil
	}
	return s.Deliver(ctx, d, item, p)
}

// Deliver sends one action to an integration, whatever its state.
func (s *WebhookSender) Deliver(ctx context.Context, d *Delivery, item state.OutboxItem, p *engine.ActionPayload) (Outcome, error) {
	client := s.Client
	if client == nil {
		client = NewWebhookClient(false)
	}
	if s.ConsoleURL != "" {
		d.Link = strings.TrimRight(s.ConsoleURL, "/") + "/organizations/" + p.OrganizationID + "/alerts"
	}
	switch d.Kind {
	case "email":
		if s.Email == nil {
			return "", Permanent(errors.New("email is not configured on this platform (RULES_SMTP_HOST)"))
		}
		if err := s.Email.Send(ctx, d.Config.Recipients, headline(d, p), messageText(d, p), item.Key); err != nil {
			return "", err
		}
		return Delivered, nil
	case "telegram":
		tg := s.Telegram
		if tg == nil {
			tg = &Telegram{Client: client}
		}
		if err := tg.Send(ctx, d.Secret, d.Config.ChatID, headline(d, p)+"\n\n"+messageText(d, p)); err != nil {
			return "", err
		}
		return Delivered, nil
	}
	u, err := url.Parse(d.URL)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" {
		return "", Permanent(fmt.Errorf("integration URL is not an http(s) URL"))
	}
	body, err := webhookBody(d, item, p)
	if err != nil {
		return "", Permanent(err)
	}
	timeout := p.Action.Timeout.D()
	if timeout <= 0 {
		timeout = defaultWebhookTimeout
	}
	if timeout > maxWebhookTimeout {
		timeout = maxWebhookTimeout
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), bytes.NewReader(body))
	if err != nil {
		return "", Permanent(errors.New("integration URL is not valid"))
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "MeteorCloud-Rules/1")
	req.Header.Set("X-MeteorCloud-Event", "rule."+p.Transition.Kind)
	req.Header.Set("X-MeteorCloud-Delivery", item.Key)
	if d.Secret != "" {
		now := time.Now
		if s.Now != nil {
			now = s.Now
		}
		req.Header.Set(SignatureHeader, Sign(d.Secret, now().Unix(), body))
	}
	resp, err := client.Do(req)
	if err != nil {
		// The URL may be a credential (Slack), and url.Error prints it: keep only the cause,
		// because errors are logged and stored.
		var ue *url.Error
		if errors.As(err, &ue) {
			err = fmt.Errorf("webhook: %w", ue.Err)
		}
		var opErr *net.OpError
		if errors.As(err, &opErr) && strings.Contains(err.Error(), "is not public") {
			return "", Permanent(err)
		}
		return "", err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
	if resp.StatusCode >= 300 && resp.StatusCode < 400 {
		return "", Permanent(fmt.Errorf("webhook answered HTTP %d; redirects are not followed", resp.StatusCode))
	}
	// Without the answer's body: it belongs to the receiver and errors are shown and stored.
	if err := statusError("webhook", resp, false); err != nil {
		return "", err
	}
	return Delivered, nil
}

// Sign returns the signature header value for body.
func Sign(secret string, unix int64, body []byte) string {
	t := strconv.FormatInt(unix, 10)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(t + "."))
	mac.Write(body)
	return "t=" + t + ",v1=" + hex.EncodeToString(mac.Sum(nil))
}

func webhookBody(d *Delivery, item state.OutboxItem, p *engine.ActionPayload) ([]byte, error) {
	if d.Kind == "slack" {
		return json.Marshal(map[string]string{"text": headline(d, p) + "\n" + messageText(d, p)})
	}
	return json.Marshal(map[string]any{
		"idempotency_key": item.Key,
		"rule_id":         p.RuleID,
		"rule_version":    p.RuleVersion,
		"rule_name":       p.RuleName,
		"organization_id": p.OrganizationID,
		"device_id":       p.DeviceID,
		"device_name":     d.DeviceName,
		"severity":        p.Severity,
		"event":           p.Transition,
		"params":          p.Action.Params,
	})
}
