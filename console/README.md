# MeteorCloud operator console

React UI for fleet, organizations, and the MQTT test page. The browser talks
**only** to the control-plane API (`VITE_API_BASE_URL`). It never calls the
data-plane host.

## Stack

- React + TypeScript
- Vite
- Tailwind CSS
- TanStack Query
- React Router
- shadcn-style UI primitives

## Local development

```bash
# From the repository root:
make install-console
make dev

# Or run Vite directly:
cd console
npm install
npm run dev
```
