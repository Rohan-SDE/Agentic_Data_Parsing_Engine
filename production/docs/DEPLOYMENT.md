# Production deployment — 1.1.0

Target: a single Linux host with Docker Engine, Compose v2 and persistent storage. Begin with at least 4 GiB RAM and two CPU cores, then size using your measured workload. Ollama needs additional resources. This release does not implement multi-host storage or high availability.

## Configure and start

Create DNS for the server and permit TCP 80/443. Keep the database, API and model service private. From the project root:

```bash
python scripts/configure_production.py --domain data.your-domain.example --email ops@your-domain.example
docker compose --env-file .env.production -f compose.production.yaml config --quiet
docker compose --env-file .env.production -f compose.production.yaml up --build -d
docker compose --env-file .env.production -f compose.production.yaml exec api python -m engine.cli create-user rohan --admin
```

Use **compose.production.yaml by itself**. It is not an overlay. The separate development file binds an HTTP API to loopback and must not be merged into production.

The configuration generator creates fresh random database credentials without putting them in command arguments, application environment variables or terminal output. It refuses existing configuration. Preserve `.secrets/` with its private parent-directory permissions; file-backed Compose secrets retain host file ownership, so individual files are readable by their granted containers. Keep `.env.production` private too. Neither belongs in Git. Store an encrypted recovery copy in your secret manager. These files are not automatically rotated or encrypted on the host.

The database bootstrap creates a separate runtime role with no superuser, role-management, database-creation or schema-creation privileges. Only the one-shot migration service receives the owner connection secret. This bootstrap runs on a **fresh PostgreSQL volume**. For an existing 1.0 installation, preserve its existing database password, provision the runtime role and grants from `deploy/init-runtime-role.sql` under the owner account with writers stopped, and configure the matching new runtime secret before starting 1.1. Do not regenerate credentials against an existing volume or delete the volume to force bootstrap.

Production starts PostgreSQL, a one-shot migration, API, worker, scheduled maintenance, and Caddy. The API has no host port. Caddy handles HTTPS and forwards only through the private network; the API trusts its explicit address, not wildcard forwarded headers. The static proxy address is outside the dynamic address pool. If the default subnet conflicts with your network, configure `ADPE_PROXY_SUBNET`, `ADPE_PROXY_DYNAMIC_RANGE`, and `ADPE_PROXY_IP` together before starting.

Application containers run as UID/GID 10001, with a read-only root, temporary storage, dropped capabilities, PID/memory/CPU limits and no privilege escalation. Application files use a named volume; PostgreSQL and Caddy use separate volumes. The API refuses a stale schema at production startup. Readiness checks schema, DB, file storage and a recent worker heartbeat. Each worker's own health check checks its identity and heartbeat.

Visit `https://YOUR_DOMAIN` and sign in using the password you chose. Public DNS/ACME and firewall configuration must be validated on your actual host. Caddy's local-CA TLS path is exercised separately by the supplied CI test; it is not proof of a public certificate.

## Release acceptance

The GitHub workflow requires all these jobs for `release-gate` to succeed:

- Functional/security tests and real PostgreSQL concurrency tests, including idempotency, storage quotas and rate limits.
- Development Compose integration and production Compose acceptance with certificate verification, restricted runtime DB privileges, secure cookies, disabled public API docs, process restarts, matched backup/restore, source checksums and retained reports.
- High/critical vulnerability scans of application, PostgreSQL and Caddy images. Findings fail the gate; nothing is automatically suppressed.
- Chromium acceptance with desktop/mobile captures, real API/worker, report downloads and browser error checks.

CI uploads evidence even when a job fails. It does not deploy anything. Configure branch protection to require `release-gate`. A checked-in workflow is not evidence that it has passed: see `VERIFICATION.md` for this delivery's actual results.

After a green workflow, record and deploy the exact approved image digests. `ADPE_APP_IMAGE`, `ADPE_POSTGRES_IMAGE`, and `ADPE_CADDY_IMAGE` can use digest references; `Dockerfile` also accepts `PYTHON_BASE` as a build argument. Default tags are build inputs, not immutable release records. Update locks and images through reviewed changes and rerun all gates.

Before admitting real users:

1. Check `/health/ready` over the public HTTPS hostname, Secure/HttpOnly/SameSite cookies, and disabled `/docs` and `/openapi.json`.
2. Analyze the included telemetry fixture: nine temperature breaches, two voltage breaches and ten missing altitude values.
3. Measure representative file sizes, concurrent users, queue wait and memory on this host; set alert thresholds and quotas to those results. The included benchmark is not a capacity guarantee.
4. Restore a backup on a separate recovery host, verify reports and record recovery time. Schedule encrypted off-host backups and test them regularly.
5. If enabling Ollama, test the installed model and a forced outage. Deterministic fallback must remain usable.
6. Assign monitoring/incident ownership and retention requirements. Verify disk alarms and log forwarding.

## Scaling and updates

```bash
docker compose --env-file .env.production -f compose.production.yaml up -d --scale worker=2
```

Each worker runs one bounded child process and needs its own memory allowance. The API and workers must share the same data volume. Keep host time synchronized for lease timestamps. Aggregate API readiness can remain healthy with one working replica, while per-container worker health detects the failed replica.

Take a matched backup before upgrades. Stop writers, run migrations using the new approved image, then start API/workers/maintenance and smoke-test. Migration `0002` adds durable idempotency keys without rewriting migration `0001`. For rollback, prefer a reviewed forward fix; restoring a matched backup is safer than an untested destructive schema downgrade.

For operations and recovery commands, read [OPERATIONS.md](OPERATIONS.md). Docker restarts are process recovery, not disaster recovery or high availability.

References: [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/), [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).

## Container security updates

The candidate uses Python 3.12.14 and PostgreSQL 17.11 on Alpine 3.23. The proxy builds Caddy 2.11.4 with Go 1.26.8 and patched module versions in `deploy/Dockerfile.caddy`; the upstream prebuilt binary had high-severity findings. CI scans the actual rebuilt proxy as well as the app and database, without vulnerability suppressions.

When changing an existing PostgreSQL deployment between Debian/glibc and Alpine/musl, restore a logical backup into a fresh volume and validate locale/collation-dependent results. Do not attach an existing Debian data volume to the Alpine image as an untested in-place upgrade. Keep the prior image and volume available until the restore is accepted.

PostgreSQL also uses `deploy/Dockerfile.postgres`: its official entrypoint is retained, and gosu 1.19 is rebuilt from a pinned upstream commit with Go 1.26.8. This fixes findings in the bundled helper without removing its privilege-drop behavior. CI scans this rebuilt database image.

## Public self-registration

Set `ADPE_PUBLIC_REGISTRATION=true` in `.env.production` and recreate application containers to enable the login page's Create account form. The default is false. Existing accounts and datasets are retained; no schema migration is required. Public registration always creates a non-admin account, uses the same password rules and hashing as administrator provisioning, and enforces five attempts per client IP per hour and fifty globally per hour (fixed windows). Duplicate username races return a conflict rather than a server error. Existing ownership, upload quotas, session and CSRF protections apply.

Accounts use usernames, not verified email addresses. Email verification, email-based password recovery and CAPTCHA are not implemented. Operators should monitor signup abuse and storage usage; per-user limits do not bound aggregate usage across many accounts. Disable registration with the same setting set to false if needed; existing users can still sign in. Administrative password reset remains available through the CLI. Public registration is an opt-in capability, not proof of unlimited public-service capacity.

## Email, Google and mobile accounts

Optional verified email codes, Google OAuth and SMS codes are documented in [EXTERNAL_AUTH.md](EXTERNAL_AUTH.md). This upgrade requires schema migration 0003 and operator-managed provider setup. All new methods are disabled by default.
