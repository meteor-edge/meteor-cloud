# MeteorCloud website

Public Next.js site: landing, about, contact, and documentation. **Not** the operator
console. It does not call the control-plane API.

## Run

```bash
# from repository root
make install-website
make dev-website

# or
cd website
npm install
npm run dev
```

http://localhost:3000

## Content

Markdown lives in `content/docs/`. `WEBSITE_CONTENT_SOURCE=filesystem` (default).
`database` is reserved for a later CMS. That database must be owned by this app,
not the control-plane Postgres.

Optional: `SITE_URL` (canonical URLs, sitemap), `CONTACT_WEBHOOK_URL` (contact form delivery).
