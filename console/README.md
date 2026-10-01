# MeteorCloud operator console

React UI for fleet, organizations, and the MQTT test page. The browser talks
**only** to the control-plane API (`VITE_API_BASE_URL`). It never calls the
data-plane host. Documentation opens on the separate website
(`VITE_DOCS_BASE_URL`, default `http://localhost:3000/docs`).

## Stack

- React + TypeScript
- Vite
- Tailwind CSS
- TanStack Query
- React Router
- shadcn-style UI primitives

## Local development

```bash
# From the MeteorCloud repository root:
make install-console
make dev-console

# Or run Vite directly:
cd console
npm install
npm run dev
```
