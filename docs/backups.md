# Backups and restore

What holds state, and what each install method backs up:

| Data | Where | Compose / Ansible | Helm |
| --- | --- | --- | --- |
| PostgreSQL (organizations, devices, releases, audit log, …) | bundled `postgres` service or your database | `pg_dump -Fc` nightly | optional nightly `pg_dump` CronJob (bundled only) |
| ClickHouse (telemetry, events, inference) | `clickhouse` service (profile `clickhouse`) | schema + Native dump per table, nightly | not yet: use ClickHouse `BACKUP` or your provider |
| Artifacts | filesystem volume or bundled MinIO | `tar.gz` of the volume, nightly | your bucket or PVC: use its snapshots/versioning |
| NATS JetStream, Redis, broker state | in-flight queues and caches | not backed up (rebuilt on start) | not backed up |

An external database or bucket is never touched: use your provider's backups for it.

Backups contain password hashes, encrypted secrets and device credential hashes. They are
written with mode `0600` under a `0750` directory; keep copies just as private.

## VM installs (`make up`, Ansible)

Ansible installs `/usr/local/sbin/meteorcloud-backup` and a systemd timer
`meteorcloud-backup.timer` that runs every night (03:15 plus up to 10 minutes,
caught up after downtime). Each run writes `/opt/edge-platform/backups/<UTC timestamp>/`
and deletes local backups older than the retention.

| Variable | Default | Meaning |
| --- | --- | --- |
| `backup_enabled` | `true` | Enable the nightly timer |
| `backup_on_calendar` | `*-*-* 03:15:00` | systemd `OnCalendar` expression |
| `backup_retention_days` | `7` | Local backups kept (0 keeps all) |
| `backup_s3_uri` | empty | `s3://bucket/prefix`: also copy each backup there |
| `backup_s3_endpoint_url` | empty | S3-compatible endpoint (MinIO, R2, Ceph) |
| `backup_s3_region` | empty | Region for the upload |
| `EDGE_PLATFORM_BACKUP_S3_ACCESS_KEY_ID` / `..._SECRET_ACCESS_KEY` / `..._SESSION_TOKEN` | env | Keys for the upload (session token for temporary credentials) |

The upload runs the `amazon/aws-cli` image. Containers cannot use the EC2 instance
role (the metadata service guard blocks them), so give keys with write-only access to
the prefix, and use bucket lifecycle rules for remote retention.

Backup and restore share `/opt/edge-platform/backups/.lock` so a timer cannot run
during a restore.

```bash
sudo systemctl start meteorcloud-backup          # back up now
sudo journalctl -u meteorcloud-backup -n 50      # last run
ls /opt/edge-platform/backups
```

### Restore on a VM

The restore replaces the current database, ClickHouse tables and artifacts. Application
services are stopped while data is replaced and started again afterwards.
`/opt/edge-platform/backup.env` is a systemd EnvironmentFile (not for shell `source`).

```bash
sudo systemd-run --wait --collect \
  --property=EnvironmentFile=/opt/edge-platform/backup.env \
  /usr/local/sbin/meteorcloud-restore --yes /opt/edge-platform/backups/20261116T031500Z
```

To move to a new host: install the same version there with the same secrets
(`~/.meteorcloud/secrets.env`, so encrypted secrets still decrypt), copy the backup
directory over, and run the restore.

## Local Compose (`make dev`)

```bash
make backup                                              # deploy/compose/backups/<timestamp>/
make restore BACKUP=deploy/compose/backups/20261116T101500Z
```

`scripts/backup.sh` and `scripts/restore.sh` work with any Compose project: run them
from the directory with `docker-compose.yml`, or set `COMPOSE_DIR` and `COMPOSE` (the
full `docker compose -f … -f …` command). See the header of each script for all settings.

## Kubernetes (Helm)

With the bundled PostgreSQL, turn on the nightly dump:

```yaml
postgresql:
  bundled:
    backup:
      enabled: true
      schedule: "15 3 * * *"
      retentionDays: 7
      persistence: { size: 8Gi }
```

Dumps go to the PVC `<release>-postgresql-backups`, which is kept when the release is
uninstalled. It is off by default because `helm install --wait` waits for the new volume,
which storage classes with `WaitForFirstConsumer` bind only when the first job runs.
Copy dumps off the cluster as well (for example with a volume snapshot schedule).

Run one now:

```bash
kubectl create job --from=cronjob/<release>-postgresql-backup backup-now
```

The CronJob name is truncated to 52 characters when the release name is long.

Restore (release `meteorcloud`, default database names): stop the API, start a helper pod
that mounts the backup volume, and run `pg_restore` from it.

```bash
kubectl scale deploy/meteorcloud-backend --replicas=0
kubectl apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata: { name: pg-restore }
spec:
  restartPolicy: Never
  securityContext: { runAsUser: 70, runAsGroup: 70 }
  containers:
    - name: restore
      image: postgres:16-alpine
      command: [sleep, "3600"]
      env:
        - { name: PGHOST, value: meteorcloud-postgresql }
        - { name: PGUSER, value: edge }
        - { name: PGDATABASE, value: edge_platform }
        - name: PGPASSWORD
          valueFrom: { secretKeyRef: { name: meteorcloud, key: POSTGRES_PASSWORD } }
      volumeMounts: [{ name: backups, mountPath: /backups }]
  volumes:
    - name: backups
      persistentVolumeClaim: { claimName: meteorcloud-postgresql-backups }
YAML
kubectl wait --for=condition=Ready pod/pg-restore
kubectl exec pg-restore -- ls /backups
kubectl exec pg-restore -- pg_restore --clean --if-exists --no-owner -d edge_platform /backups/20261116T031500Z.dump
kubectl delete pod pg-restore
kubectl scale deploy/meteorcloud-backend --replicas=1
```

The secret is named after the release (`meteorcloud` here) unless you set `secrets.existingSecret`.

With an external database, rely on your provider's point-in-time recovery.

## Tested in CI

`make compose-smoke` (CI job "Docker Compose") creates a user, an organization and an
artifact, runs `scripts/backup.sh`, deletes every volume, checks that the user is gone,
runs `scripts/restore.sh`, and checks that the user can log in and the artifact downloads
byte for byte.
