package identity

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"meteorcloud/meteorcli/internal/api"
)

// Defaults for Service. Tests shorten them.
const (
	// DefaultMinPoll is the fastest an enrollment request is polled (contract: 10 s).
	DefaultMinPoll = 10 * time.Second
	// DefaultRetryWait is the wait after a failed poll or submission.
	DefaultRetryWait = 30 * time.Second
	// DefaultAuthRetryMin and DefaultAuthRetryMax space out checks of a
	// refused token, as the Python service did (30 s doubling to 15 min).
	DefaultAuthRetryMin = 30 * time.Second
	DefaultAuthRetryMax = 15 * time.Minute
	// DefaultTick is how often Run reports progress while idle.
	DefaultTick = 30 * time.Second
	// DefaultClaimGrace bounds how long Claim waits past its deadline for the
	// first answer of a poll (a zero wait means "one check").
	DefaultClaimGrace = 45 * time.Second
)

// Events tells the rest of the agent what identity did. Each may be nil.
type Events struct {
	// Enrolled: there is a new identity (or a refused one was accepted again).
	Enrolled func()
	// CredentialsRotated: the MQTT credentials changed or MQTT was turned on or off.
	CredentialsRotated func()
	// ConfigChanged: config.json changed (another server or pinned CA).
	ConfigChanged func()
	// Tick: the loop is alive (for the systemd watchdog).
	Tick func()
}

// Service enrolls the device and keeps its credentials current. The socket
// calls Register, RequestToken, Claim, Test and Reload; Run does the
// background work. Enrollment changes run one at a time.
type Service struct {
	Source *Source
	API    *api.Client
	// Inventory returns the facts sent with register and enroll/request.
	Inventory func() api.Inventory
	Log       *slog.Logger
	Events    Events

	MinPoll, RetryWait, AuthRetryMin, AuthRetryMax, Tick, ClaimGrace time.Duration

	mu           sync.Mutex // one enrollment change at a time; guards the fields below
	nextPoll     time.Time
	lastPoll     time.Time
	backoffUntil time.Time // the server's Retry-After for polls; Claim never polls earlier
	approved     *approval // an approval whose identity could not be stored yet
	autoAsk      bool      // submit a request by itself (API key saved before start)
	nextAsk      time.Time
	authWait     time.Duration
	nextAuth     time.Time
	nextMQTT     time.Time
	wake         chan struct{}
	wakeOnce     sync.Once
	reloadDue    bool
	// applied is the server the API client talks to; serverErr why the
	// configured one could not be used (then the client has no server at all).
	applied   string
	serverOK  bool
	serverErr error
}

// approval is a collected approval kept until its identity is stored: the
// server hands the device token out once. It holds a secret; never log it.
type approval struct {
	enrollment api.Enrollment
	claimID    string
}

func (s *Service) defaults() {
	s.wakeOnce.Do(func() {
		s.wake = make(chan struct{}, 1)
		if s.MinPoll <= 0 {
			s.MinPoll = DefaultMinPoll
		}
		if s.RetryWait <= 0 {
			s.RetryWait = DefaultRetryWait
		}
		if s.AuthRetryMin <= 0 {
			s.AuthRetryMin = DefaultAuthRetryMin
		}
		if s.AuthRetryMax <= 0 {
			s.AuthRetryMax = DefaultAuthRetryMax
		}
		if s.Tick <= 0 {
			s.Tick = DefaultTick
		}
		if s.ClaimGrace <= 0 {
			s.ClaimGrace = DefaultClaimGrace
		}
		if s.Inventory == nil {
			s.Inventory = func() api.Inventory { return api.Inventory{} }
		}
	})
}

// poke wakes Run without blocking.
func (s *Service) poke() {
	s.defaults()
	select {
	case s.wake <- struct{}{}:
	default:
	}
}

// Run loads the identity, then polls a pending request, checks a refused token
// and refreshes MQTT credentials until ctx ends. It watches the identity
// directory and config.json, so files written by other tools are picked up.
func (s *Service) Run(ctx context.Context) error {
	s.defaults()
	s.mu.Lock()
	s.reload()
	sn := s.Source.snapshot()
	now := time.Now()
	s.nextPoll, s.nextMQTT = now, now
	// An API key saved before the agent started (the imager, a meteorenv file,
	// `config --api-key` before install) means: ask to be enrolled by itself.
	// A key the server already refused for this is not sent again at every start.
	s.autoAsk = !sn.enrolled && sn.pending == nil && sn.apiKey != "" && sn.server != "" && !s.Source.autoRefused(sn.apiKey)
	s.nextAsk = now
	s.mu.Unlock()

	var wg sync.WaitGroup
	defer wg.Wait()
	watchCtx, stopWatch := context.WithCancel(ctx)
	defer stopWatch()
	wg.Add(1)
	go func() { // owned: ends with watchCtx
		defer wg.Done()
		s.watch(watchCtx)
	}()

	tick := time.NewTicker(s.Tick)
	defer tick.Stop()
	timer := time.NewTimer(0)
	defer timer.Stop()
	for {
		s.tick()
		s.mu.Lock()
		next := s.step(ctx)
		s.mu.Unlock()
		if !timer.Stop() {
			select {
			case <-timer.C:
			default:
			}
		}
		timer.Reset(time.Until(next))
		select {
		case <-ctx.Done():
			return nil
		case <-s.wake:
		case <-s.Source.authFailed:
			s.mu.Lock()
			s.authWait = s.AuthRetryMin
			s.nextAuth = time.Now().Add(s.authWait)
			s.mu.Unlock()
			s.Log.Error("the server refused this device's credential (deleted or revoked in MeteorCloud?); "+
				"checking again later. To enroll again: 'meteorcli request-token --new' or 'meteorcli register'",
				"retry_in", s.AuthRetryMin)
		case <-timer.C:
		case <-tick.C:
		}
	}
}

func (s *Service) tick() {
	if s.Events.Tick != nil {
		s.Events.Tick()
	}
}

// step does the background work that is due and returns when to wake next.
// The caller holds s.mu.
func (s *Service) step(ctx context.Context) time.Time {
	if s.reloadDue {
		s.reload()
	}
	now := time.Now()
	next := now.Add(s.Tick)
	sn := s.Source.snapshot()
	switch {
	case s.approved != nil:
		if !now.Before(s.nextPoll) {
			s.retryApproved()
		}
		next = earliest(next, s.nextPoll)
	case sn.pending != nil:
		if !now.Before(s.nextPoll) {
			if s.pollOnce(ctx) == PollExpired {
				s.resubmit(ctx)
			}
		}
		next = earliest(next, s.nextPoll)
	case s.autoAsk && !sn.enrolled && sn.server != "" && sn.apiKey != "":
		if !now.Before(s.nextAsk) {
			s.autoSubmit(ctx, sn)
		}
		next = earliest(next, s.nextAsk)
	}
	sn = s.Source.snapshot()
	switch {
	case sn.enrolled && sn.rejected:
		if !s.nextAuth.IsZero() && !now.Before(s.nextAuth) {
			s.checkAuth(ctx, sn.token)
		}
		if !s.nextAuth.IsZero() {
			next = earliest(next, s.nextAuth)
		}
	case sn.enrolled:
		if !now.Before(s.nextMQTT) {
			s.syncMQTT(ctx, sn)
			s.nextMQTT = time.Now().Add(max(s.Source.Heartbeat(), time.Minute))
		}
		next = earliest(next, s.nextMQTT)
	}
	return next
}

// reload rereads config.json and the identity files and tells the agent what
// changed. The caller holds s.mu.
func (s *Service) reload() {
	s.reloadDue = false
	c, err := s.Source.load()
	if err != nil {
		s.Log.Error("identity files", "err", err)
	}
	if c.retry {
		s.reloadDue = true
	}
	sn := s.Source.snapshot()
	if c.server || !s.serverOK {
		s.applyServer(sn, c.server)
	}
	if c.pending {
		s.nextPoll = time.Now()
	}
	if c.identity {
		s.nextMQTT = time.Now()
		s.Log.Info("identity loaded", "device_id", s.Source.DeviceID())
		if s.Events.Enrolled != nil {
			s.Events.Enrolled()
		}
	}
	if c.unenrolled {
		s.Log.Warn("identity files removed; waiting to be enrolled again")
	}
}

// applyServer points the API client at the configured server. When that
// fails (an unreadable pinned CA, say) the client gets no server at all, so no
// request can reach the previous one, and it is tried again at the next step.
// The caller holds s.mu.
func (s *Service) applyServer(sn snapshot, changed bool) {
	if err := s.API.SetServer(sn.server, sn.caFile); err != nil {
		_ = s.API.SetServer("", "")
		if changed || s.serverErr == nil || s.serverErr.Error() != err.Error() {
			s.Log.Error("server settings unusable; no requests are sent until they are fixed", "server", sn.server, "err", err)
		}
		s.applied, s.serverOK, s.serverErr = "", false, err
		s.reloadDue = true
		return
	}
	recovered := s.serverErr != nil
	s.applied, s.serverOK, s.serverErr = sn.server, true, nil
	if changed || recovered {
		s.Log.Info("server settings loaded", "server", sn.server)
		if s.Events.ConfigChanged != nil {
			s.Events.ConfigChanged()
		}
	}
}

// target returns the identity when the API client talks to its server, the
// only case in which a credential may be sent. The caller holds s.mu.
func (s *Service) target() (snapshot, error) {
	sn := s.Source.snapshot()
	switch {
	case sn.server == "":
		return sn, errNotConfigured()
	case !s.serverOK || s.applied != sn.server:
		return sn, errorf(CodeNotConfigured, "the server settings cannot be used: %v", s.serverErr)
	}
	return sn, nil
}

// Reload rereads config.json and the identity files now (`meteorcli config`
// asks for it after saving). It returns the identity status afterwards.
func (s *Service) Reload(ctx context.Context) Status {
	s.defaults()
	s.mu.Lock()
	s.reload()
	s.mu.Unlock()
	s.poke()
	return s.Source.Status()
}

// requestReload makes Run reread the files (inotify saw a change).
func (s *Service) requestReload() {
	s.mu.Lock()
	s.reloadDue = true
	s.mu.Unlock()
	s.poke()
}

// checkAuth asks the server whether a refused token works again. GET /mqtt is
// cheap and needs the device token; 404 (an older server) also proves it.
func (s *Service) checkAuth(ctx context.Context, token string) {
	_, err := s.API.MQTTStatus(ctx, token)
	if err == nil || api.StatusOf(err) == 404 {
		if s.Source.accept(token) {
			s.nextAuth = time.Time{}
			s.Log.Info("the server accepts this device's credential again")
			if s.Events.Enrolled != nil {
				s.Events.Enrolled()
			}
		}
		return
	}
	if api.IsAuth(err) {
		s.authWait = min(max(s.authWait*2, s.AuthRetryMin), s.AuthRetryMax)
	}
	s.nextAuth = time.Now().Add(max(s.authWait, s.AuthRetryMin))
	s.Log.Warn("credential still refused", "err", err, "retry_in", time.Until(s.nextAuth).Round(time.Second))
}

// syncMQTT keeps the MQTT credentials at the server's config version, as the
// Python agent did on every cycle: GET /mqtt, and when the version differs
// POST /mqtt/credentials (the old password stops working).
func (s *Service) syncMQTT(ctx context.Context, sn snapshot) {
	st, err := s.API.MQTTStatus(ctx, sn.token)
	switch {
	case api.IsAuth(err):
		s.Source.AuthFailed(sn.token)
		return
	case api.StatusOf(err) == 404:
		return // an older server: keep the stored settings
	case err != nil:
		s.Log.Warn("could not check MQTT settings", "err", err)
		return
	}
	if !st.Enabled {
		if s.Source.setMQTTOff(true) {
			s.Log.Info("MQTT is turned off on the server")
			s.rotated()
		}
		return
	}
	wanted := ""
	if st.ConfigVersion != nil {
		wanted = *st.ConfigVersion
	}
	if wanted == "" || wanted == sn.mqttVersion {
		if s.Source.setMQTTOff(false) {
			s.rotated()
		}
		return
	}
	// New credentials: MQTT stays as it is (off, or the old ones) until they are
	// stored; saveMQTT turns it on.
	creds, err := s.API.MQTTCredentials(ctx, sn.token)
	switch {
	case api.IsAuth(err):
		s.Source.AuthFailed(sn.token)
		return
	case err != nil:
		s.Log.Warn("could not fetch MQTT credentials", "err", err)
		return
	}
	tls := creds.TLS
	if creds.Password == "" || (tls != nil && !*tls) {
		s.Log.Warn("the server sent MQTT settings without a password or without TLS; ignored")
		return
	}
	if err := s.Source.saveMQTT(creds.MQTTConnect, creds.ConfigVersion); err != nil {
		s.Log.Error("store MQTT credentials", "err", err)
		return
	}
	s.Log.Info("MQTT settings changed on the server; new credentials stored", "config_version", creds.ConfigVersion)
	s.rotated()
}

func (s *Service) rotated() {
	if s.Events.CredentialsRotated != nil {
		s.Events.CredentialsRotated()
	}
}

func earliest(a, b time.Time) time.Time {
	if b.Before(a) {
		return b
	}
	return a
}
