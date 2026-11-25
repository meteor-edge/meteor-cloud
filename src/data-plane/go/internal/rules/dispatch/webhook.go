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
	"sort"
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
	Kind     string `json:"kind"` // webhook | slack
	Secret   string `json:"secret"`
	Enabled  bool   `json:"enabled"`
	Silenced bool   `json:"silenced"`
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

// WebhookSender posts webhooks and notifications. Its HTTP client refuses private,
// loopback and link-local addresses (checked on the resolved address at connect
// time, so DNS tricks do not help) unless AllowPrivate is set, and never follows
// redirects.
type WebhookSender struct {
	Resolver Resolver
	Client   *http.Client
	Now      func() time.Time
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

var cgnat = netip.MustParsePrefix("100.64.0.0/10")

func blocked(ip netip.Addr) bool {
	ip = ip.Unmap()
	return ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() ||
		ip.IsInterfaceLocalMulticast() || ip.IsMulticast() || ip.IsUnspecified() || cgnat.Contains(ip)
}

func (s *WebhookSender) Send(ctx context.Context, item state.OutboxItem, p *engine.ActionPayload) (Outcome, error) {
	d, err := s.Resolver.Resolve(ctx, item, p)
	if err != nil {
		return "", err
	}
	if !d.Enabled || d.Silenced {
		return Skipped, nil
	}
	u, err := url.Parse(d.URL)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" {
		return "", Permanent(fmt.Errorf("integration URL is not an http(s) URL"))
	}
	body, err := webhookBody(d.Kind, item, p)
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
		return "", Permanent(err)
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
	client := s.Client
	if client == nil {
		client = NewWebhookClient(false)
	}
	resp, err := client.Do(req)
	if err != nil {
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
	if err := statusError("webhook", resp); err != nil {
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

func webhookBody(kind string, item state.OutboxItem, p *engine.ActionPayload) ([]byte, error) {
	if kind == "slack" {
		return json.Marshal(map[string]string{"text": slackText(p)})
	}
	return json.Marshal(map[string]any{
		"idempotency_key": item.Key,
		"rule_id":         p.RuleID,
		"rule_version":    p.RuleVersion,
		"rule_name":       p.RuleName,
		"organization_id": p.OrganizationID,
		"device_id":       p.DeviceID,
		"severity":        p.Severity,
		"event":           p.Transition,
		"params":          p.Action.Params,
	})
}

func slackText(p *engine.ActionPayload) string {
	var b strings.Builder
	fmt.Fprintf(&b, "[%s] %s %s on device %s", strings.ToUpper(p.Severity), p.RuleName, p.Transition.Kind, p.DeviceID)
	if p.Transition.Reason != "" {
		fmt.Fprintf(&b, " (%s)", p.Transition.Reason)
	}
	keys := make([]string, 0, len(p.Transition.Values))
	for k := range p.Transition.Values {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for i, k := range keys {
		sep := ", "
		if i == 0 {
			sep = ": "
		}
		fmt.Fprintf(&b, "%s%s=%g", sep, k, p.Transition.Values[k])
	}
	return b.String()
}
