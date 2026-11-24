package deploy

import (
	"context"

	"meteorcloud/meteorcli/internal/api"
	"meteorcloud/meteorcli/internal/credstore"
	"meteorcloud/meteorcli/internal/deploy/runtime"
)

// Application credentials for workloads bound to a MeteorCloud application
// (spec.meteorcloud.application) live in internal/credstore. Deploy obtains
// one before it starts a workload and deletes the ones no longer desired; the
// appcreds module rotates them before they expire, so the running container
// sees a new token without a restart (and without deploy running).

// binding returns the item's application binding, nil when it has none.
func binding(it item) *credstore.Binding {
	b, _ := it.spec()["meteorcloud"].(map[string]any)
	if b == nil || !runtime.Spec(b).Bool("application", false) {
		return nil
	}
	env := ""
	if v := b["environment"]; v != nil && v != "" {
		env = runtime.Stringify(v)
	}
	return &credstore.Binding{Application: runtime.Stringify(b["application"]), Environment: env}
}

// creds is deploy's view of the credential store.
type creds struct{ store credstore.Store }

func toApp(c *credstore.Credential) *runtime.App {
	if c == nil {
		return nil
	}
	return &runtime.App{TokenFile: c.TokenFile, Env: c.Env}
}

// forApply returns the credential to start a workload with: a valid stored
// one, otherwise a new one (the stored one while offline, until it expires).
func (c creds) forApply(ctx context.Context, client *api.Client, token string, it item) (*runtime.App, error) {
	b := binding(it)
	if b == nil {
		return nil, nil
	}
	s, key := c.store, it.key()
	cur := s.Current(key, *b)
	if cur != nil && !s.RefreshDue(key) {
		return toApp(cur), nil
	}
	issued, err := s.Mint(ctx, client, token, key, *b, false)
	if err != nil && cur != nil && !s.Expired(key) && !api.IsAuth(err) {
		return toApp(cur), nil // offline: the stored token still works
	}
	return toApp(issued), err
}

// present reports whether a running workload still has its credential. It
// returns false when the workload must be applied again (token lost or
// binding changed).
func (c creds) present(it item) bool {
	b := binding(it)
	return b == nil || c.store.Current(it.key(), *b) != nil
}

// prune deletes stored tokens of workloads no longer desired or no longer bound.
func (c creds) prune(keep map[string]bool) { c.store.Prune(keep) }
