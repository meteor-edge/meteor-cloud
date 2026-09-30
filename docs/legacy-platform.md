# MeteorCloud website (legacy note)

Older layouts used `backend/` and `frontend/` under a `platform/` tree. The live modules are `control-plane/`, `data-plane/`, `console/`, and `website/`.

The application never knows how it was installed. The installer lives in `infrastructure/installer/` and treats enabled **services** (`cloud_app`, `vpn`) as deployable units, separate from the application Compose modules.
