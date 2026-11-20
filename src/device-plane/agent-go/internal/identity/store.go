package identity

// The Source's writes and the snapshot with secrets: only Service uses them.

import (
	"errors"
	"fmt"
	"os"
	"time"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/config"
)

// errStale means the enrollment answer belongs to a server or request that
// was replaced meanwhile (`meteorcli config` switched servers): it is dropped.
var errStale = errors.New("identity: the server or the enrollment request changed meanwhile; the answer was discarded")

// commit stores a new identity from server and makes it current. claimID,
// when set, is the pending request the identity answers. Under the identity
// lock it checks that config.json still names server and that the request is
// still the one on disk, so a credential is only ever stored for the server
// that issued it.
func (s *Source) commit(e api.Enrollment, server, claimID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := os.MkdirAll(s.files.p.IdentityDir, 0o700); err != nil {
		return fmt.Errorf("identity: %w", err)
	}
	unlock, err := config.LockIdentity(s.files.p)
	if err != nil {
		return err
	}
	defer unlock()
	cfg, err := config.Load(s.files.p.ConfigFile)
	if err != nil || cfg.Server() != server || server == "" {
		return errStale
	}
	if claimID != "" {
		if c, _ := s.files.loadPending(); c == nil || c.RequestID != claimID {
			return errStale
		}
	}
	if err := s.files.commit(e); err != nil {
		return err
	}
	dev, err := s.files.loadDevice()
	if err != nil {
		return err
	}
	mqtt, err := s.files.loadMQTT()
	if err != nil {
		return err
	}
	s.device, s.token, s.mqtt = dev, e.DeviceToken, mqtt
	s.pending, s.apiKey, s.rejected, s.outcome, s.mqttOff = nil, "", false, nil, false
	s.notifyLocked()
	return nil
}

// savePending stores a new pending request (replacing an older one).
func (s *Source) savePending(c *PendingClaim) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.files.savePending(c); err != nil {
		return err
	}
	s.pending, s.outcome, s.checked = c, nil, time.Time{}
	s.notifyLocked()
	return nil
}

// dropPending removes the pending request and remembers how it ended.
func (s *Source) dropPending(o *Outcome) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.files.remove(s.files.p.PendingClaimFile()); err != nil {
		return err
	}
	s.pending, s.outcome = nil, o
	s.notifyLocked()
	return nil
}

// markChecked records that request was just polled. A check of a request
// that was replaced or ended meanwhile is ignored.
func (s *Source) markChecked(requestID string, at time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.pending == nil || s.pending.RequestID != requestID {
		return
	}
	s.checked = at
	s.notifyLocked()
}

// autoRefused and markAutoRefused remember, across restarts, that the server
// refused key for the request the agent makes by itself.
func (s *Source) autoRefused(key string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.files.autoRefused(key)
}

func (s *Source) markAutoRefused(key string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.files.markAutoRefused(key)
}

// saveAPIKey stores an enrollment API key given on the command line.
func (s *Source) saveAPIKey(key string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.files.writeSecret(s.files.p.APIKeyFile(), key); err != nil {
		return err
	}
	s.apiKey = key
	return nil
}

// saveMQTT stores new broker credentials from the server.
func (s *Source) saveMQTT(c api.MQTTConnect, configVersion string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	var keep = config.Object(nil)
	if s.mqtt != nil {
		keep = s.mqtt.extra
	}
	v := configVersion
	if err := s.files.writeMQTT(s.files.p.IdentityDir, c, &v, keep); err != nil {
		return err
	}
	m, err := s.files.loadMQTT()
	if err != nil {
		return err
	}
	s.mqtt, s.mqttOff = m, false
	s.notifyLocked()
	return nil
}

// setMQTTOff records whether the server turned MQTT off; it reports a change.
func (s *Source) setMQTTOff(off bool) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.mqttOff == off {
		return false
	}
	s.mqttOff = off
	s.notifyLocked()
	return true
}

// accept clears a refusal after the server took the token again.
func (s *Source) accept(token string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.rejected || token != s.token {
		return false
	}
	s.rejected = false
	s.notifyLocked()
	return true
}

// snapshot returns what Service needs for one decision, with the secrets.
type snapshot struct {
	enrolled, rejected bool
	token, apiKey      string
	pending            *PendingClaim
	mqttVersion        string
	server             string
	caFile             string
	enrollName         string
}

func (s *Source) snapshot() snapshot {
	s.mu.Lock()
	defer s.mu.Unlock()
	sn := snapshot{
		enrolled: s.enrolledLocked(), rejected: s.rejected, token: s.token, apiKey: s.apiKey,
		server: s.cfg.Server(), caFile: caFile(s.cfg),
	}
	if s.cfg != nil {
		sn.enrollName = s.cfg.EnrollName
	}
	if s.pending != nil {
		c := *s.pending
		sn.pending = &c
	}
	if s.mqtt != nil && s.mqtt.ConfigVersion != nil {
		sn.mqttVersion = *s.mqtt.ConfigVersion
	}
	return sn
}

func caFile(c *config.Config) string {
	if c == nil {
		return ""
	}
	return c.CAFile
}
