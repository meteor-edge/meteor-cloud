# MeteorCloud website (legacy note)

Older layouts used `backend/` and `frontend/` under a `platform/` tree. The live modules in this repo are `src/control-plane/`, `src/data-plane/`, `src/device-plane/agent/`, and `console/`.

The application never knows how it was installed. The installer lives in `infrastructure/installer/` and treats enabled **services** (`cloud_app`, `vpn`) as deployable units, separate from the application Compose modules.
