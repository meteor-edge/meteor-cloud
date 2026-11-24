package bus

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"net"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/eclipse/paho.golang/packets"
	"github.com/eclipse/paho.golang/paho"

	"meteorcloud/meteorcli/internal/api"
)

// Limits of one connection.
const (
	// connectTimeout bounds the TCP and TLS handshake, CONNECT and SUBSCRIBE.
	connectTimeout = 15 * time.Second
	// keepAlive is the MQTT keep-alive in seconds (the Python agent used 60).
	keepAlive = 60
	// goodbyeTimeout bounds the offline publish on a clean stop.
	goodbyeTimeout = 2 * time.Second
	// stableAfter is how long a connection must last before the retry delay
	// starts again at its minimum.
	stableAfter = time.Minute
)

// Settings is how to reach the broker. Password is a secret: never log it.
type Settings struct {
	DeviceID string
	Host     string // also the name checked in the broker's certificate
	Port     int
	Username string
	Password string
	// CAFile is the broker CA the server sent at enrollment; empty means the
	// system CAs.
	CAFile string
	// Will is published retained on Status by the broker when the connection
	// drops, and by Run itself on a clean stop. A string keeps Settings
	// comparable, so the mqtt module can tell whether anything changed.
	Will string
}

// Broker returns host:port, for logs and status.
func (s Settings) Broker() string { return net.JoinHostPort(s.Host, strconv.Itoa(s.Port)) }

// Run keeps one MQTT session open until ctx ends: it connects (TLS 1.2+, the
// stored CA, client id device-<id>), subscribes to the commands topic, and
// after a lost connection or a failed attempt tries again after 1 s doubling
// to 30 s, with jitter. onUp (may be nil) runs once per connection, in its own
// goroutine, with a context that ends with that connection; it publishes the
// online status. On a clean stop Run publishes the will itself and
// disconnects, so the broker keeps "offline" without waiting for a timeout.
// Run returns nil once ctx ended.
func (r *Router) Run(ctx context.Context, s Settings, onUp func(ctx context.Context)) error {
	if s.DeviceID == "" || s.Host == "" || s.Port <= 0 {
		return errors.New("bus: incomplete MQTT settings")
	}
	tlsCfg, err := tlsConfig(s)
	if err != nil {
		return err
	}
	r.setDeviceID(s.DeviceID)
	backoff := &api.Backoff{Min: time.Second, Max: 30 * time.Second}
	for {
		started := time.Now()
		connected, err := r.session(ctx, s, tlsCfg, onUp)
		if ctx.Err() != nil {
			r.setDown(s.Broker(), nil)
			return nil
		}
		if connected && time.Since(started) > stableAfter {
			backoff.Reset()
		}
		wait := backoff.Next(nil)
		r.setDown(s.Broker(), err)
		if connected {
			r.log.Warn("MQTT connection lost; reconnecting", "broker", s.Broker(), "err", err, "retry_in", wait.Round(time.Millisecond))
		} else {
			r.log.Warn("MQTT connect failed", "broker", s.Broker(), "err", err, "retry_in", wait.Round(time.Millisecond))
		}
		t := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			t.Stop()
			r.setDown(s.Broker(), nil)
			return nil
		case <-t.C:
		}
	}
}

// session runs one connection until it is lost or ctx ends. connected says
// whether it got as far as a subscribed session.
func (r *Router) session(ctx context.Context, s Settings, tlsCfg *tls.Config, onUp func(context.Context)) (connected bool, err error) {
	dctx, cancel := context.WithTimeout(ctx, connectTimeout)
	defer cancel()
	dialer := &tls.Dialer{NetDialer: &net.Dialer{Timeout: connectTimeout}, Config: tlsCfg}
	raw, err := dialer.DialContext(dctx, "tcp", s.Broker())
	if err != nil {
		return false, explainDial(err)
	}
	defer raw.Close()

	lost := make(chan error, 1)
	report := func(err error) {
		select {
		case lost <- err:
		default:
		}
	}
	c := paho.NewClient(paho.ClientConfig{
		ClientID: "device-" + s.DeviceID,
		Conn:     packets.NewThreadSafeConn(raw),
		OnPublishReceived: []func(paho.PublishReceived) (bool, error){func(pr paho.PublishReceived) (bool, error) {
			r.deliver(pr.Packet.Topic, pr.Packet.Payload)
			return true, nil
		}},
		OnClientError: func(err error) { report(err) },
		OnServerDisconnect: func(d *paho.Disconnect) {
			report(fmt.Errorf("the broker closed the session (reason code %d)", d.ReasonCode))
		},
		PacketTimeout: publishTimeout,
	})
	status := Topic(s.DeviceID, Status)
	ack, err := c.Connect(dctx, &paho.Connect{
		ClientID:     "device-" + s.DeviceID,
		KeepAlive:    keepAlive,
		CleanStart:   true,
		Username:     s.Username,
		UsernameFlag: true,
		Password:     []byte(s.Password),
		PasswordFlag: true,
		WillMessage:  &paho.WillMessage{Topic: status, Payload: []byte(s.Will), QoS: 1, Retain: true},
	})
	if err != nil {
		if ack != nil && (ack.ReasonCode == 0x86 || ack.ReasonCode == 0x87) {
			return false, fmt.Errorf("the broker refused the MQTT credentials (reason code %d); the agent fetches new ones when the server's MQTT config changes", ack.ReasonCode)
		}
		return false, fmt.Errorf("MQTT connect: %w", err)
	}
	if _, err := c.Subscribe(dctx, &paho.Subscribe{Subscriptions: []paho.SubscribeOptions{{Topic: Topic(s.DeviceID, Commands), QoS: 1}}}); err != nil {
		_ = c.Disconnect(&paho.Disconnect{ReasonCode: 0})
		return false, fmt.Errorf("MQTT subscribe: %w", err)
	}
	r.setUp(c, s.DeviceID, s.Broker())
	r.log.Info("MQTT connected; subscribed to commands", "broker", s.Broker())

	connCtx, stop := context.WithCancel(ctx)
	var wg sync.WaitGroup
	if onUp != nil {
		wg.Add(1)
		go func() { // owned: waited for below before session returns
			defer wg.Done()
			onUp(connCtx)
		}()
	}
	select {
	case <-ctx.Done():
	case err = <-lost:
	case <-c.Done():
		err = errors.New("the MQTT connection closed")
	}
	stop()
	wg.Wait()
	r.setDown(s.Broker(), err)
	if ctx.Err() != nil {
		// Clean stop: say offline ourselves (a normal DISCONNECT discards the will).
		gctx, gcancel := context.WithTimeout(context.Background(), goodbyeTimeout)
		if _, perr := c.Publish(gctx, &paho.Publish{Topic: status, Payload: []byte(s.Will), QoS: 1, Retain: true}); perr != nil {
			r.log.Debug("could not publish the offline status on stop", "err", perr)
		}
		gcancel()
		_ = c.Disconnect(&paho.Disconnect{ReasonCode: 0})
		return true, nil
	}
	// Lost: close the socket so paho's goroutines end, and wait for them.
	raw.Close()
	select {
	case <-c.Done():
	case <-time.After(goodbyeTimeout):
	}
	return true, err
}

// tlsConfig trusts the broker CA from enrollment, or the system CAs when the
// server sent none. It never skips verification.
func tlsConfig(s Settings) (*tls.Config, error) {
	cfg := &tls.Config{MinVersion: tls.VersionTLS12, ServerName: s.Host}
	if s.CAFile == "" {
		return cfg, nil
	}
	pem, err := os.ReadFile(s.CAFile)
	if err != nil {
		return nil, fmt.Errorf("bus: read the broker CA: %w", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pem) {
		return nil, fmt.Errorf("bus: %s holds no certificate", s.CAFile)
	}
	cfg.RootCAs = pool
	return cfg, nil
}

// explainDial adds the hint the Python agent gave for a certificate mismatch.
func explainDial(err error) error {
	var hostErr x509.HostnameError
	var authErr x509.UnknownAuthorityError
	switch {
	case errors.As(err, &hostErr):
		return fmt.Errorf("MQTT TLS: %w (the broker certificate must name this address; set MQTT_PUBLIC_HOST on the server)", err)
	case errors.As(err, &authErr):
		return fmt.Errorf("MQTT TLS: %w (the broker certificate is not signed by the CA from enrollment)", err)
	}
	return fmt.Errorf("MQTT broker not reachable: %w", err)
}
