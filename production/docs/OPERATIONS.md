# Operations runbook

All examples run from the repository root. For every production command use `docker compose --env-file .env.production -f compose.production.yaml`. This is a standalone configuration, not an overlay. Shorter Compose examples below are for local development.

## Routine checks

```bash
docker compose ps
docker compose logs --tail 100 api worker
docker compose exec worker python -m engine.cli worker-health
```

Probe `/health/live` for process liveness and `/health/ready` for DB/storage/recent-worker readiness. Logs are structured JSON from the application and include request IDs, status codes, timings and job IDs. Uvicorn access logs are disabled to avoid exposing filename query parameters. Audit events persist in PostgreSQL. Forward logs/audit records to a protected central service if your retention policy requires tamper resistance.

Monitor volume free space, PostgreSQL availability/connections, service restarts, oldest queued job age, running lease age, failures, response errors and worker heartbeat age. Suggested initial alerts: readiness fails for more than one minute, free disk below 20%, queue age beyond twice the configured job timeout, or sustained increase in failed jobs. Tune based on measured usage.

SQL for operators:

```sql
SELECT status, count(*) FROM jobs GROUP BY status;
SELECT min(created_at) AS oldest_queued_epoch FROM jobs WHERE status = 'queued';
SELECT id, attempts, lease_until FROM jobs WHERE status = 'running';
```

There is no Prometheus exporter in this release. Use health probes, logs and DB queries, or add a reviewed metrics adapter.

## Account operations

```bash
python -m engine.cli create-user analyst --admin
python -m engine.cli reset-password analyst
```

Passwords are prompted, not command-line arguments. Resetting a password revokes that user's sessions. Create and disable accounts through the administrator dashboard. Disabling a user immediately blocks API access and revokes sessions; already queued/running jobs can still finish. Dataset access remains owner-only. Disabled-user retention/deletion requires an operator-reviewed maintenance procedure; the API does not grant administrators blanket data access.

## Cleanup and retention

```bash
docker compose exec -T api python -m engine.cli cleanup
```

The production maintenance service runs cleanup daily and retries failures after one minute; local installations can use a host scheduler. Cleanup removes expired sessions/rate buckets, heartbeats older than 24 hours, and unreferenced upload/work files older than 24 hours. It does not delete registered datasets, reports, audit events or watcher archives. Maximum job runtime is one hour, so the 24-hour work-file margin avoids normal active-job deletion.

Users delete datasets and associated reports in the dashboard. Define your own retention periods before deployment. `processed/` and `rejected/` contain additional raw data and are **not included in account storage quotas**; monitor and archive/delete these operator-owned directories according to policy. Report retention is tied to dataset deletion. Secure deletion on SSD/cloud volumes requires provider encryption/key-retirement controls; unlinking files is not secure erasure.

## Consistent backup

Back up PostgreSQL and the application data volume together. The safe baseline is a short maintenance window that stops every writer. Use encrypted, access-controlled off-host storage; the examples create local artifacts only and do not encrypt them.

The cross-platform helper streams binary dumps directly, stops every running application writer, writes a checksum manifest, and resumes those writers even if a dump fails. It refuses an existing backup destination. Failed attempts remain in a `.partial` directory for inspection and are not valid backups. Do not run overlapping backup/update operations or allow external DB/file writers during the maintenance window.

```bash
mkdir -p backups
chmod 700 backups
python scripts/recovery.py backup backups/release-1-1 --project adpe-production
python scripts/recovery.py verify backups/release-1-1
```

The archive contains sensitive source data, report metadata and password hashes. Keep the backup directory private, encrypt before off-host transfer and restrict access. Checksums detect damage; they do not authenticate an untrusted backup. Backup `.env.production` and `.secrets/` separately into your encrypted secret store. Record the approved image digests with each recovery set; the manifest records source image IDs, application version and schema.

## Restore drill

Use a separate recovery host, matching application image, configuration and secrets. The helper refuses any existing resources with the target project label, checks archive hashes and rejects escaping paths, links and device files before starting recovery. It restores into a fresh database without a destructive `--clean`, checks every registered raw-file checksum, then starts API and worker. It leaves the public proxy and watcher stopped.

```bash
python scripts/recovery.py restore backups/release-1-1 --project adpe-recovery-20260923
```

Validate account access, completed reports and a fresh synthetic analysis before opening the recovery proxy. Use the same explicit recovery project name in subsequent commands. Do not rerun restore into a partial recovery project; inspect it and choose another fresh project. No existing project is automatically deleted. A same-host drill must use a non-overlapping proxy subnet/range/address and unused ports, or stop the source stack without deleting its volumes. `production_smoke.py` manages these boundaries only for its own disposable synthetic CI projects.

Record actual recovery time and data loss window, then reopen access after validation. Keep the original volumes/backups until recovery is accepted. To verify storage independently with writers stopped:

```bash
docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps api python -m engine.cli verify-storage
```

Restored sessions remain valid until their expiry unless revoked. After an incident-related restore, revoke all sessions (`DELETE FROM auth_sessions;`) and reset affected account/database credentials through a reviewed procedure.

## Troubleshooting

| Symptom | Action |
|---|---|
| Jobs remain queued | Start the worker with the same DB URL and data directory as the API; check readiness and logs. |
| Worker exits unexpectedly | Check memory limits, available disk, parser bounds and model timeout. The job will retry up to the configured attempts. |
| DataError / malformed input | Use UTF-8, consistent CSV headers/rows and flat JSON. Inspect the original; do not blindly rerun unchanged invalid data. |
| Ollama fallback | Confirm the configured host is reachable from the worker and the named model is installed. Fallback reports remain usable. |
| Sign-in loops in production | Use the exact HTTPS origin, check Secure cookies, proxy scheme forwarding and allowed hosts. |
| CSRF error | Refresh the page to get the current session token; avoid mixing localhost and 127.0.0.1 origins. |
| 429 | Wait for Retry-After; investigate repeated login attempts and correct trusted proxy boundaries. |
| Watcher ignores files | Set an active watcher user, avoid symlinks and `.part`/`.tmp` files, wait for stability, check the quota and watcher lock. |
| Permission denied in Docker | Ensure persistent storage is writable by UID/GID 10001; preserve PostgreSQL's own required ownership separately. |
| PDF shows `?` for some names | Built-in PDF fonts use Latin-1; JSON/Markdown preserve full Unicode. Add licensed Unicode fonts for your locale before requiring Unicode PDFs. |

No production secrets, certificates, trained model weights or live deployment accounts are included with the source.
