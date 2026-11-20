// Package fakebroker is a tiny MQTT 5 broker over TLS for tests: it accepts
// any login, answers SUBSCRIBE, PUBLISH (QoS 1), PINGREQ and DISCONNECT, keeps
// what clients published and the will of the last connect, and can be stopped
// and started on the same port to test reconnects. It routes nothing between
// clients; a test sends a command with Send. Never used outside tests.
package fakebroker

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/eclipse/paho.golang/packets"
)

// Publish is one message a client published.
type Publish struct {
	ClientID string
	Topic    string
	Payload  string
	Retain   bool
	QoS      byte
}

// Connect is what a client sent in CONNECT.
type Connect struct {
	ClientID  string
	Username  string
	Password  string
	WillTopic string
	Will      string
}

// client is one connection: its id and a lock so two writers never interleave.
type client struct {
	id string
	mu sync.Mutex
}

func (cl *client) write(c net.Conn, cp *packets.ControlPacket) error {
	cl.mu.Lock()
	defer cl.mu.Unlock()
	_, err := cp.WriteTo(c)
	return err
}

// Broker is the fake broker.
type Broker struct {
	// Addr is host:port; it stays the same across Stop and Start.
	Addr string
	// CAFile is a PEM file with the CA that signed the broker certificate.
	CAFile string
	// Refuse, when set, answers CONNECT with this reason code (0x86: bad login).
	Refuse byte

	tlsCfg *tls.Config

	mu        sync.Mutex
	ln        net.Listener
	conns     map[net.Conn]*client
	published []Publish
	connects  []Connect
	subs      map[net.Conn][]string
	changed   chan struct{}
	wg        sync.WaitGroup
}

// Start makes a CA and a certificate for 127.0.0.1 in dir and listens.
func Start(dir string) (*Broker, error) {
	b := &Broker{conns: map[net.Conn]*client{}, subs: map[net.Conn][]string{}, changed: make(chan struct{})}
	if err := b.makeCerts(dir); err != nil {
		return nil, err
	}
	ln, err := tls.Listen("tcp", "127.0.0.1:0", b.tlsCfg)
	if err != nil {
		return nil, err
	}
	b.Addr = ln.Addr().String()
	b.serve(ln)
	return b, nil
}

// Stop closes the listener and every connection (the broker is "down").
func (b *Broker) Stop() {
	b.mu.Lock()
	ln := b.ln
	b.ln = nil
	for c := range b.conns {
		c.Close()
	}
	b.mu.Unlock()
	if ln != nil {
		ln.Close()
	}
	b.wg.Wait()
}

// Restart listens again on the same address after Stop.
func (b *Broker) Restart() error {
	var ln net.Listener
	var err error
	for i := 0; i < 50; i++ { // the port may linger for a moment
		if ln, err = tls.Listen("tcp", b.Addr, b.tlsCfg); err == nil {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err != nil {
		return err
	}
	b.serve(ln)
	return nil
}

// Published returns a copy of everything clients published.
func (b *Broker) Published() []Publish {
	b.mu.Lock()
	defer b.mu.Unlock()
	return append([]Publish(nil), b.published...)
}

// Connects returns every CONNECT seen.
func (b *Broker) Connects() []Connect {
	b.mu.Lock()
	defer b.mu.Unlock()
	return append([]Connect(nil), b.connects...)
}

// Wait waits until cond (called under no lock) is true or timeout passes.
func (b *Broker) Wait(timeout time.Duration, cond func() bool) bool {
	deadline := time.After(timeout)
	for {
		b.mu.Lock()
		ch := b.changed
		b.mu.Unlock()
		if cond() {
			return true
		}
		select {
		case <-ch:
		case <-deadline:
			return cond()
		case <-time.After(50 * time.Millisecond):
		}
	}
}

// Send publishes payload on topic to every client subscribed to it (exact match).
func (b *Broker) Send(topic, payload string) int {
	b.mu.Lock()
	defer b.mu.Unlock()
	sent := 0
	for c, topics := range b.subs {
		for _, t := range topics {
			if t == topic {
				cp := packets.NewControlPacket(packets.PUBLISH)
				cp.Content = &packets.Publish{Topic: topic, Payload: []byte(payload), QoS: 0}
				if cl := b.conns[c]; cl != nil && cl.write(c, cp) == nil {
					sent++
				}
			}
		}
	}
	return sent
}

func (b *Broker) serve(ln net.Listener) {
	b.mu.Lock()
	b.ln = ln
	b.mu.Unlock()
	b.wg.Add(1)
	go func() {
		defer b.wg.Done()
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			b.wg.Add(1)
			go func() {
				defer b.wg.Done()
				b.handle(c)
			}()
		}
	}()
}

func (b *Broker) notify() {
	close(b.changed)
	b.changed = make(chan struct{})
}

func (b *Broker) handle(c net.Conn) {
	cl := &client{}
	b.mu.Lock()
	b.conns[c] = cl
	b.mu.Unlock()
	defer func() {
		b.mu.Lock()
		delete(b.conns, c)
		delete(b.subs, c)
		b.notify()
		b.mu.Unlock()
		c.Close()
	}()
	write := func(cp *packets.ControlPacket) error { return cl.write(c, cp) }
	for {
		cp, err := packets.ReadPacket(c)
		if err != nil {
			return
		}
		switch p := cp.Content.(type) {
		case *packets.Connect:
			b.mu.Lock()
			cl.id = p.ClientID
			b.connects = append(b.connects, Connect{ClientID: p.ClientID, Username: p.Username, Password: string(p.Password), WillTopic: p.WillTopic, Will: string(p.WillMessage)})
			refuse := b.Refuse
			b.notify()
			b.mu.Unlock()
			ack := packets.NewControlPacket(packets.CONNACK)
			ack.Content.(*packets.Connack).ReasonCode = refuse
			if write(ack) != nil || refuse != 0 {
				return
			}
		case *packets.Subscribe:
			reasons := make([]byte, len(p.Subscriptions))
			b.mu.Lock()
			for i, s := range p.Subscriptions {
				b.subs[c] = append(b.subs[c], s.Topic)
				reasons[i] = s.QoS
			}
			b.notify()
			b.mu.Unlock()
			ack := packets.NewControlPacket(packets.SUBACK)
			ack.Content = &packets.Suback{PacketID: p.PacketID, Reasons: reasons}
			if write(ack) != nil {
				return
			}
		case *packets.Publish:
			b.mu.Lock()
			b.published = append(b.published, Publish{ClientID: cl.id, Topic: p.Topic, Payload: string(p.Payload), Retain: p.Retain, QoS: p.QoS})
			b.notify()
			b.mu.Unlock()
			if p.QoS == 1 {
				ack := packets.NewControlPacket(packets.PUBACK)
				ack.Content = &packets.Puback{PacketID: p.PacketID}
				if write(ack) != nil {
					return
				}
			}
		case *packets.Pingreq:
			if write(packets.NewControlPacket(packets.PINGRESP)) != nil {
				return
			}
		case *packets.Disconnect:
			return
		}
	}
}

func (b *Broker) makeCerts(dir string) error {
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	ca := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "fakebroker-ca"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(24 * time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if err != nil {
		return err
	}
	caCert, err := x509.ParseCertificate(caDER)
	if err != nil {
		return err
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	leaf := &x509.Certificate{
		SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "127.0.0.1"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(24 * time.Hour),
		IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}, DNSNames: []string{"localhost"},
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	leafDER, err := x509.CreateCertificate(rand.Reader, leaf, caCert, &key.PublicKey, caKey)
	if err != nil {
		return err
	}
	b.CAFile = filepath.Join(dir, "ca.pem")
	if err := os.WriteFile(b.CAFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}), 0o600); err != nil {
		return err
	}
	if len(leafDER) == 0 {
		return errors.New("fakebroker: no certificate")
	}
	b.tlsCfg = &tls.Config{
		MinVersion:   tls.VersionTLS12,
		Certificates: []tls.Certificate{{Certificate: [][]byte{leafDER}, PrivateKey: key}},
	}
	return nil
}
