# Operator console

Source lives in the private repo **[meteor-edge/meteor-ui](https://github.com/meteor-edge/meteor-ui)**.

This directory is a placeholder on the public MeteorCloud tree. Local source is
cloned into `ui/` (gitignored), which has `origin` set to meteor-ui.

```bash
make checkout-ui
make install-console
make dev-console
```

Work in `ui/console`. Push to the UI repo (not MeteorCloud):

```bash
git -C ui status
git -C ui add console
git -C ui commit -m "..."
git -C ui push
```

Docs link: `VITE_DOCS_BASE_URL` (default `http://localhost:3000/docs`) on the
website app in the same private repo.

See [docs/frontends.md](../docs/frontends.md).
