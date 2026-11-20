// Package identity owns who this device is: enrollment (registration token, or
// API key plus approval), the device token, the MQTT credentials and their
// refresh, and what a 401/403 means. Other modules read the identity through a
// Source and report a refused credential with AuthFailed; nobody else touches
// the identity files or decides to stop using a token.
//
// Service performs the enrollment the CLI asks for over the agent socket
// (register, request-token, claim), polls a pending request in the background
// until it is approved (also across reboots), and tells the rest of the agent
// through Events. This package does not import the agent core; the core wraps
// Service in a module and turns Events into signals.
package identity

import (
	"errors"
	"fmt"
	"io/fs"
	"sync"
	"time"

	"meteorcloud/meteorcli/internal/config"
)

// Identity states, as `meteorcli status` shows them.
const (
	StateNotConfigured = "not_configured"
	StateWaiting       = "waiting_for_enrollment"
	StatePending       = "pending_approval"
	StateEnrolled      = "enrolled"
	StateRejected      = "credential_rejected"
)

var (
	// ErrNotEnrolled means there is no device token yet: wait for Enrolled.
	ErrNotEnrolled = errors.New("device is not enrolled")
	// ErrRejected means the server refused the token: wait until identity
	// confirms it again or the device is enrolled again.
	ErrRejected = errors.New("the server refused this device's credential")
)

// Outcome is how the last enrollment request ended without an identity
// (rejected, expired, unknown to the server). Claim reports it.
type Outcome struct {
	RequestID string
	Status    string
	Reason    string
}

// Status is a copy of the identity for status and the socket. It holds no secrets.
type Status struct {
	State            string
	Server           string
	Domain           string
	DeviceID         string
	OrganizationID   string
	Name             string
	HeartbeatSeconds int
	Credential       bool
	APIKey           bool
	MQTT             bool
	PendingRequestID string
	PendingChecked   time.Time
	Outcome          *Outcome
}

// Source is the identity as the rest of the agent sees it: loaded from the
// identity directory, changed only by Service and by AuthFailed. Safe for
// concurrent use; every file write happens under its lock, so two writers
// never interleave.
type Source struct {
	files files

	mu       sync.Mutex
	cfg      *config.Config
	device   *config.Device
	token    string
	mqtt     *MQTT
	mqttOff  bool
	pending  *PendingClaim
	checked  time.Time
	apiKey   string
	rejected bool
	outcome  *Outcome
	changed  chan struct{}
	// authFailed tells Service that AuthFailed switched to rejected.
	authFailed chan struct{}
}

// NewSource returns a Source for the files in p. Call Service.Run (or Load)
// before relying on it.
func NewSource(p config.Paths) *Source {
	return &Source{
		files:      files{p: p, now: time.Now},
		changed:    make(chan struct{}),
		authFailed: make(chan struct{}, 1),
	}
}

// Token returns the device token. It fails with ErrNotEnrolled or ErrRejected;
// callers then wait for the Enrolled signal instead of calling the server.
func (s *Source) Token() (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case s.token == "" || s.device == nil || s.device.DeviceID == "":
		return "", ErrNotEnrolled
	case s.rejected:
		return "", ErrRejected
	}
	return s.token, nil
}

// Enrolled reports whether the device has an identity (even a refused one).
func (s *Source) Enrolled() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.enrolledLocked()
}

func (s *Source) enrolledLocked() bool {
	return s.token != "" && s.device != nil && s.device.DeviceID != ""
}

// DeviceID returns the device id, "" until enrolled.
func (s *Source) DeviceID() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.device == nil {
		return ""
	}
	return s.device.DeviceID
}

// Heartbeat returns the heartbeat interval: the server's, never below the minimum.
func (s *Source) Heartbeat() time.Duration {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.device.Heartbeat()
}

// SetHeartbeat stores the interval a heartbeat answer asked for. It is written
// to device.json only when it changed, so the next start uses it too.
func (s *Source) SetHeartbeat(seconds int) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if seconds <= 0 || s.device == nil || s.device.HeartbeatIntervalSeconds == seconds {
		return nil
	}
	// Change a copy: if the write fails, the next answer tries again.
	dev := *s.device
	dev.HeartbeatIntervalSeconds = seconds
	if err := dev.Save(s.files.p.DeviceFile(), config.Owner{}); err != nil {
		return fmt.Errorf("identity: heartbeat interval: %w", err)
	}
	s.device = &dev
	return nil
}

// MQTT returns the broker settings, and false when there are none or the
// server turned MQTT off. The password is a secret: never log it.
func (s *Source) MQTT() (MQTT, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.mqtt == nil || s.mqttOff {
		return MQTT{}, false
	}
	return *s.mqtt, true
}

// AuthFailed is how a module reports that the server answered 401 or 403 to
// token. Identity stops handing the token out and checks it again later, with
// backoff, instead of every module hammering the server. A report about an
// older token (already replaced) is ignored.
func (s *Source) AuthFailed(token string) {
	s.mu.Lock()
	if token == "" || token != s.token || s.rejected {
		s.mu.Unlock()
		return
	}
	s.rejected = true
	s.notifyLocked()
	s.mu.Unlock()
	select {
	case s.authFailed <- struct{}{}:
	default:
	}
}

// Changed returns a channel that is closed at the next change of the identity
// (or of a pending request's last check). Take a new channel after each change.
func (s *Source) Changed() <-chan struct{} {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.changed
}

// Status returns a copy of the identity without secrets.
func (s *Source) Status() Status {
	s.mu.Lock()
	defer s.mu.Unlock()
	st := Status{
		Server:           s.cfg.Server(),
		HeartbeatSeconds: int(s.device.Heartbeat().Seconds()),
		Credential:       s.token != "",
		APIKey:           s.apiKey != "",
		MQTT:             s.mqtt != nil,
		PendingChecked:   s.checked,
	}
	if s.cfg != nil {
		st.Domain = s.cfg.Domain
	}
	if s.device != nil {
		st.DeviceID, st.OrganizationID, st.Name = s.device.DeviceID, s.device.OrganizationID, s.device.Name
	}
	if s.pending != nil {
		st.PendingRequestID = s.pending.RequestID
	}
	if s.outcome != nil {
		o := *s.outcome
		st.Outcome = &o
	}
	switch {
	case s.enrolledLocked() && s.rejected:
		st.State = StateRejected
	case s.enrolledLocked():
		st.State = StateEnrolled
	case s.pending != nil:
		st.State = StatePending
	case st.Server != "":
		st.State = StateWaiting
	default:
		st.State = StateNotConfigured
	}
	return st
}

// notifyLocked wakes everyone waiting on Changed. The caller holds s.mu.
func (s *Source) notifyLocked() {
	close(s.changed)
	s.changed = make(chan struct{})
}

// change describes what a reload found different.
type change struct {
	server     bool // the server or pinned CA changed
	identity   bool // a new or different device token
	pending    bool // a pending request appeared or changed
	unenrolled bool // the identity files are gone
	retry      bool // an interrupted commit could not be finished: load again later
}

// Load reads config.json and the identity directory, finishing an interrupted
// commit first. A damaged file is moved aside and that part starts clean; the
// error says so. The agent loads through Service, which also acts on changes.
func (s *Source) Load() error {
	_, err := s.load()
	return err
}

// load is Load that also reports what changed since the last load.
func (s *Source) load() (change, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var errs []error
	cfg, err := config.Load(s.files.p.ConfigFile)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		errs = append(errs, err)
	}
	if err := s.finishStagingLocked(); err != nil {
		// The live files may be half moved (a new token beside the old
		// device.json): keep what is in memory and try again later.
		var c change
		c.server = s.cfg.Server() != cfg.Server() || caFile(s.cfg) != caFile(cfg)
		c.retry = true
		s.cfg = cfg
		if c.server {
			s.notifyLocked()
		}
		return c, errors.Join(append(errs, err)...)
	}
	dev, err := s.files.loadDevice()
	errs = append(errs, err)
	token, err := s.files.readSecret(s.files.p.DeviceTokenFile())
	errs = append(errs, err)
	mqtt, err := s.files.loadMQTT()
	errs = append(errs, err)
	pending, err := s.files.loadPending()
	errs = append(errs, err)
	key, err := s.files.readSecret(s.files.p.APIKeyFile())
	errs = append(errs, err)

	var c change
	c.server = s.cfg.Server() != cfg.Server() || caFile(s.cfg) != caFile(cfg)
	wasEnrolled, oldToken := s.enrolledLocked(), s.token
	oldPending := ""
	if s.pending != nil {
		oldPending = s.pending.RequestID
	}

	s.cfg, s.device, s.token, s.mqtt, s.pending, s.apiKey = cfg, dev, token, mqtt, pending, key
	nowEnrolled := s.enrolledLocked()
	c.identity = nowEnrolled && (!wasEnrolled || token != oldToken)
	c.unenrolled = wasEnrolled && !nowEnrolled
	if c.identity || c.unenrolled {
		s.rejected = false
	}
	newPending := ""
	if pending != nil {
		newPending = pending.RequestID
	}
	c.pending = newPending != "" && newPending != oldPending
	if newPending != oldPending {
		s.checked = time.Time{} // a check of the old request says nothing about the new one
	}
	if c != (change{}) || newPending != oldPending {
		s.notifyLocked() // also wakes Claim when the request file was removed
	}
	return c, errors.Join(errs...)
}

// finishStagingLocked finishes or drops an interrupted commit under the
// identity lock, so `meteorcli config` cannot drop the files halfway.
func (s *Source) finishStagingLocked() error {
	unlock, err := config.LockIdentity(s.files.p)
	if err != nil {
		return err
	}
	defer unlock()
	return s.files.finishStaging()
}
