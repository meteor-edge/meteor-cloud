# Edge Platform

The control-plane application consists of:

| Area | Path | Role |
| --- | --- | --- |
| Backend | `backend/` | FastAPI API, database, auth infrastructure |
| Frontend | `frontend/` | React operator UI shell |
| Docker | `docker/` | Container build files |

The platform never knows how it was installed. The installer lives outside this
tree and treats the platform as a deployable artifact.

See the repository root README and `docs/` for architecture and development
guidance.
