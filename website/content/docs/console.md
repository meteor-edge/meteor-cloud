---
title: Console
description: Operator web UI for organizations and fleet.
section: Platform
order: 12
---

The console (`console/`) is a Vite + React app. Operators sign in, manage organizations, enroll devices, inspect last MQTT status/metrics, and use the MQTT test page.

The browser **only** calls the control plane. Set `VITE_API_BASE_URL` to that origin (locally `http://localhost:8000`). Do not point it at the data plane.

Start with `compose/console.yml` or `cd console && npm run dev`.
