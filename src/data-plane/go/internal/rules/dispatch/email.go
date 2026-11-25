package dispatch

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"errors"
	"fmt"
	"mime"
	"mime/quotedprintable"
	"net"
	"net/mail"
	"net/smtp"
	"net/textproto"
	"strings"
	"time"
)

// SMTP is the platform's outgoing mail server. Organizations only choose recipients;
// the server and the sender address belong to the operator.
type SMTP struct {
	Addr     string // host:port
	Username string
	Password string
	From     string // "Name <alerts@example.com>" or a bare address
	TLS      string // starttls (default) | tls | none
	Timeout  time.Duration
	RootCAs  *tls.Config // tests; nil uses the system roots
}

// Validate checks the settings at start-up.
func (s *SMTP) Validate() error {
	if _, _, err := net.SplitHostPort(s.Addr); err != nil {
		return fmt.Errorf("SMTP address: %w", err)
	}
	if _, err := mail.ParseAddress(s.From); err != nil {
		return fmt.Errorf("SMTP sender %q: %w", s.From, err)
	}
	switch s.TLS {
	case "", "starttls", "tls", "none":
	default:
		return fmt.Errorf("SMTP TLS mode %q: must be starttls, tls or none", s.TLS)
	}
	if s.TLS == "none" && s.Username != "" {
		return errors.New("SMTP credentials are not sent without TLS")
	}
	return nil
}

func (s *SMTP) tlsConfig(host string) *tls.Config {
	c := &tls.Config{}
	if s.RootCAs != nil {
		c = s.RootCAs.Clone()
	}
	c.ServerName = host
	c.MinVersion = tls.VersionTLS12
	return c
}

// Send delivers one message to every recipient. A 5xx answer is permanent.
func (s *SMTP) Send(ctx context.Context, to []string, subject, body, key string) error {
	from, err := mail.ParseAddress(s.From)
	if err != nil {
		return Permanent(err)
	}
	rcpt := make([]string, 0, len(to))
	for _, r := range to {
		a, err := mail.ParseAddress(r)
		if err != nil {
			return Permanent(fmt.Errorf("recipient %q: %w", r, err))
		}
		rcpt = append(rcpt, a.Address)
	}
	if len(rcpt) == 0 {
		return Permanent(errors.New("no recipients"))
	}
	host, _, _ := net.SplitHostPort(s.Addr)
	timeout := s.Timeout
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	conn, err := (&net.Dialer{}).DialContext(ctx, "tcp", s.Addr)
	if err != nil {
		return err
	}
	defer conn.Close()
	deadline, _ := ctx.Deadline()
	conn.SetDeadline(deadline)
	if s.TLS == "tls" {
		conn = tls.Client(conn, s.tlsConfig(host))
	}
	c, err := smtp.NewClient(conn, host)
	if err != nil {
		return smtpError(err)
	}
	defer c.Close()
	if s.TLS == "" || s.TLS == "starttls" {
		if ok, _ := c.Extension("STARTTLS"); !ok {
			return errors.New("the mail server does not offer STARTTLS")
		}
		if err := c.StartTLS(s.tlsConfig(host)); err != nil {
			return smtpError(err)
		}
	}
	if s.Username != "" {
		if err := c.Auth(smtp.PlainAuth("", s.Username, s.Password, host)); err != nil {
			return smtpError(err)
		}
	}
	if err := c.Mail(from.Address); err != nil {
		return smtpError(err)
	}
	for _, r := range rcpt {
		if err := c.Rcpt(r); err != nil {
			return smtpError(err)
		}
	}
	w, err := c.Data()
	if err != nil {
		return smtpError(err)
	}
	if _, err := w.Write(message(from, rcpt, subject, body, key)); err != nil {
		return err
	}
	if err := w.Close(); err != nil {
		return smtpError(err)
	}
	// The server accepted the message; a failing QUIT must not send it again.
	_ = c.Quit()
	return nil
}

func smtpError(err error) error {
	var te *textproto.Error
	if errors.As(err, &te) && te.Code >= 500 {
		return Permanent(err)
	}
	return err
}

// message builds a UTF-8 text message. The Message-ID comes from the idempotency key,
// so a redelivery carries the same id and mail clients can drop the duplicate.
func message(from *mail.Address, to []string, subject, body, key string) []byte {
	sum := sha256.Sum256([]byte(key))
	domain := from.Address[strings.LastIndex(from.Address, "@")+1:]
	var b bytes.Buffer
	header := func(k, v string) { fmt.Fprintf(&b, "%s: %s\r\n", k, v) }
	header("From", from.String())
	list := make([]string, len(to))
	for i, a := range to { // quoted where needed, so one recipient never reads as two
		list[i] = strings.Trim((&mail.Address{Address: a}).String(), "<>")
	}
	header("To", strings.Join(list, ", "))
	header("Subject", mime.QEncoding.Encode("utf-8", oneLine(subject)))
	header("Date", time.Now().UTC().Format(time.RFC1123Z))
	header("Message-ID", "<"+hex.EncodeToString(sum[:16])+"@"+domain+">")
	header("Auto-Submitted", "auto-generated")
	header("MIME-Version", "1.0")
	header("Content-Type", "text/plain; charset=utf-8")
	header("Content-Transfer-Encoding", "quoted-printable")
	b.WriteString("\r\n")
	q := quotedprintable.NewWriter(&b)
	q.Write([]byte(strings.ReplaceAll(body, "\n", "\r\n")))
	q.Close()
	return b.Bytes()
}
