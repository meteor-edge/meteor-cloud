// Package identity authenticates application credentials against a policy snapshot
// provisioned by the control plane.
//
// The data plane never calls the control plane while handling a message. It holds the
// last snapshot it fetched (applications, permissions and credential hashes) and
// keeps serving from it while the control plane is unreachable, up to a configured
// maximum staleness after which it fails closed.
package identity

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"reflect"
	"strings"
	"sync/atomic"
	"time"

	"meteorcloud/dataplane/internal/streams"
)

// TokenPrefix marks application data-plane tokens.
const TokenPrefix = "mca_"

var (
	ErrUnauthenticated = errors.New("invalid or expired credential")
	ErrPolicyStale     = errors.New("policy snapshot is unavailable or too old")
)

// Permissions is what an application may do. The zero value grants nothing.
type Permissions struct {
	Publish         []string `json:"publish"`
	Subscribe       []string `json:"subscribe"`
	Read            []string `json:"read"`
	ReceiveCommands bool     `json:"receive_commands"`
	SendCommands    []string `json:"send_commands"`
}

// Identity is an authenticated application instance. Every field comes from the
// credential, never from a request body.
type Identity struct {
	CredentialID   string
	OrganizationID string
	ApplicationID  string
	Application    string // slug
	DeviceID       string // empty for cloud-hosted instances
	Environment    string
	ExpiresAt      time.Time
	Permissions    Permissions
	tokenHash      string
}

func (id *Identity) CanPublish(stream string) bool {
	return !streams.IsReserved(stream) && streams.MatchAny(id.Permissions.Publish, stream)
}

func (id *Identity) CanRead(stream string) bool {
	return streams.MatchAny(id.Permissions.Read, stream)
}

// CanSubscribe reports whether a subscription pattern stays within the subscribe grants.
func (id *Identity) CanSubscribe(pattern string) bool {
	return streams.CoveredByAny(id.Permissions.Subscribe, pattern)
}

func (id *Identity) CanSendCommandTo(appSlug string) bool {
	for _, s := range id.Permissions.SendCommands {
		if s == appSlug {
			return true
		}
	}
	return false
}

// SnapshotApplication and SnapshotCredential mirror GET /internal/data-plane/policy.
type SnapshotApplication struct {
	ID             string      `json:"id"`
	OrganizationID string      `json:"organization_id"`
	Slug           string      `json:"slug"`
	Permissions    Permissions `json:"permissions"`
}

type SnapshotCredential struct {
	ID             string    `json:"id"`
	OrganizationID string    `json:"organization_id"`
	ApplicationID  string    `json:"application_id"`
	DeviceID       *string   `json:"device_id"`
	Environment    string    `json:"environment"`
	TokenHash      string    `json:"token_hash"`
	ExpiresAt      time.Time `json:"expires_at"`
}

type Snapshot struct {
	Version      string                `json:"version"`
	GeneratedAt  time.Time             `json:"generated_at"`
	Applications []SnapshotApplication `json:"applications"`
	Credentials  []SnapshotCredential  `json:"credentials"`
}

type index struct {
	version  string
	loadedAt time.Time
	byHash   map[string]*Identity
	byCredID map[string]*Identity
	apps     map[string]SnapshotApplication // by id
	bySlug   map[string]SnapshotApplication // by org + "/" + slug
}

// Store holds the current policy and answers authentication questions.
type Store struct {
	current  atomic.Pointer[index]
	maxStale time.Duration
	now      func() time.Time
}

// NewStore creates an empty store. maxStale <= 0 means a snapshot never becomes too old.
func NewStore(maxStale time.Duration) *Store {
	return &Store{maxStale: maxStale, now: time.Now}
}

// SetClock replaces the time source (tests).
func (s *Store) SetClock(now func() time.Time) { s.now = now }

// Apply installs a new snapshot.
func (s *Store) Apply(snap *Snapshot) {
	idx := &index{
		version:  snap.Version,
		loadedAt: s.now(),
		byHash:   make(map[string]*Identity, len(snap.Credentials)),
		byCredID: make(map[string]*Identity, len(snap.Credentials)),
		apps:     make(map[string]SnapshotApplication, len(snap.Applications)),
		bySlug:   make(map[string]SnapshotApplication, len(snap.Applications)),
	}
	for _, app := range snap.Applications {
		idx.apps[app.ID] = app
		idx.bySlug[app.OrganizationID+"/"+app.Slug] = app
	}
	for _, c := range snap.Credentials {
		app, ok := idx.apps[c.ApplicationID]
		// A credential must belong to an application of the same organization.
		if !ok || app.OrganizationID != c.OrganizationID || len(c.TokenHash) != 64 {
			continue
		}
		id := &Identity{
			CredentialID:   c.ID,
			OrganizationID: c.OrganizationID,
			ApplicationID:  c.ApplicationID,
			Application:    app.Slug,
			Environment:    c.Environment,
			ExpiresAt:      c.ExpiresAt,
			Permissions:    app.Permissions,
			tokenHash:      strings.ToLower(c.TokenHash),
		}
		if c.DeviceID != nil {
			id.DeviceID = *c.DeviceID
		}
		idx.byHash[strings.ToLower(c.TokenHash)] = id
		idx.byCredID[c.ID] = id
	}
	s.current.Store(idx)
}

// Touch records that the control plane confirmed the current snapshot is still valid.
func (s *Store) Touch() {
	cur := s.current.Load()
	if cur == nil {
		return
	}
	next := *cur
	next.loadedAt = s.now()
	s.current.Store(&next)
}

func (s *Store) usable() (*index, error) {
	idx := s.current.Load()
	if idx == nil {
		return nil, ErrPolicyStale
	}
	if s.maxStale > 0 && s.now().Sub(idx.loadedAt) > s.maxStale {
		return nil, ErrPolicyStale
	}
	return idx, nil
}

// Ready reports whether a usable snapshot is loaded.
func (s *Store) Ready() bool {
	_, err := s.usable()
	return err == nil
}

// Version returns the loaded snapshot version and when it was last confirmed.
func (s *Store) Version() (string, time.Time) {
	idx := s.current.Load()
	if idx == nil {
		return "", time.Time{}
	}
	return idx.version, idx.loadedAt
}

// HashToken returns the lowercase SHA-256 hex digest stored by the control plane.
func HashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

// Authenticate resolves a bearer token. Lookup is by hash of a high-entropy secret,
// so it does not leak timing about any stored value.
func (s *Store) Authenticate(token string) (*Identity, error) {
	idx, err := s.usable()
	if err != nil {
		return nil, err
	}
	if !strings.HasPrefix(token, TokenPrefix) || len(token) > 256 {
		return nil, ErrUnauthenticated
	}
	id, ok := idx.byHash[HashToken(token)]
	if !ok || !s.now().Before(id.ExpiresAt) {
		return nil, ErrUnauthenticated
	}
	return id, nil
}

// ByCredentialID resolves an already authenticated credential (MQTT ACL checks).
func (s *Store) ByCredentialID(credentialID string) (*Identity, error) {
	idx, err := s.usable()
	if err != nil {
		return nil, err
	}
	id, ok := idx.byCredID[credentialID]
	if !ok || !s.now().Before(id.ExpiresAt) {
		return nil, ErrUnauthenticated
	}
	return id, nil
}

// StillValid reports whether a long-lived connection may go on as it was authenticated:
// the credential is still accepted, its token was not rotated, and the identity and
// permissions it carries are unchanged. A connection that fails this is closed and
// the client reconnects with what it is allowed now.
func (s *Store) StillValid(id *Identity) bool {
	cur, err := s.ByCredentialID(id.CredentialID)
	if err != nil {
		return false
	}
	return cur == id || (cur.tokenHash == id.tokenHash && cur.OrganizationID == id.OrganizationID &&
		cur.ApplicationID == id.ApplicationID && cur.Application == id.Application &&
		cur.DeviceID == id.DeviceID && cur.Environment == id.Environment &&
		reflect.DeepEqual(cur.Permissions, id.Permissions))
}

// Application looks an enabled application up by id.
func (s *Store) Application(id string) (SnapshotApplication, bool) {
	idx, err := s.usable()
	if err != nil {
		return SnapshotApplication{}, false
	}
	app, ok := idx.apps[id]
	return app, ok
}

// ApplicationBySlug looks an enabled application up within an organization.
func (s *Store) ApplicationBySlug(orgID, slug string) (SnapshotApplication, bool) {
	idx, err := s.usable()
	if err != nil {
		return SnapshotApplication{}, false
	}
	app, ok := idx.bySlug[orgID+"/"+slug]
	return app, ok
}
