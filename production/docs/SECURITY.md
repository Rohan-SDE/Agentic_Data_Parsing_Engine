# Security model and release review

## Trust boundaries

The browser and all uploaded content are untrusted. Account credentials, server configuration, administrators, operators and the watcher folder's producers are trusted. The production network exposes Caddy only. PostgreSQL, the data volume and an optional Ollama endpoint stay private. Host-level administrators can access stored data; owner isolation applies to application/API users, not a hostile host operator.

This is a bounded diagnostic application, not a hardened malware detonation environment. It never executes uploaded files, generated Python or shell commands. Format parsing and statistical libraries still process untrusted input inside a worker subprocess/container, so patching and container resource controls remain necessary.

## Controls implemented

| Area | Controls |
|---|---|
| Credentials | Argon2id; 12–128-character passwords; no default password; CLI prompts; uniform login errors; bounded hashing concurrency |
| Sessions | 256-bit random tokens; only SHA-256 hashes stored; HttpOnly/SameSite=Strict; Secure in production; absolute expiry; logout, disable and password-reset revocation |
| Cross-site requests | Per-session CSRF token on mutations; origin allowlist; no CORS; CSP; frame denial |
| Data authorization | Every dataset/job/report query checks owner_id; admin role does not override data ownership |
| Rate limits | Atomic database counters for account/IP login, upload, job submission and report generation |
| Request bounds | Total body deadlines, bounded DB waits, raw and JSON body limits, upload bytes, field/column/row limits, per-account storage and active-job quotas |
| File handling | UUID storage names, path/control-character rejection, private permissions, atomic staging, symlink rejection in watcher |
| Agent execution | Validated tool names and reasons; no eval/exec tools; no arbitrary URLs; bounded model response; mandatory checks; deterministic fallback |
| Worker recovery | CPU/address-space limits on Linux, parent wall-clock timeout, leases, conditional result commit, bounded retries and cancellation |
| Output | Dashboard uses textContent/DOM construction rather than HTML injection; Markdown escaping; XML escaping for PDF; no inline/CDN scripts |
| Logging | No request bodies, filenames, objectives, row values or credentials in application logs; explicit audit actions |
| Deployment | Production config fails without PostgreSQL, HTTPS, secure cookies or explicit hosts; application UID 10001; read-only root and reduced capabilities |

Argon2 parameters: time cost 3, memory cost 65,536 KiB, parallelism 2. Tokens and CSRF secrets use Python's cryptographic `secrets` module. Password verification includes a dummy hash when an account is absent.

## Model threat handling

Column names and objectives may contain prompt injection. The system prompt labels them untrusted, but the primary boundary is structural: the model response must match a five-tool schema. The engine rejects unknown tools, does not execute code, and derives findings from deterministic checks. Model-generated reasons are displayed as text. Input can influence which optional tool runs, but cannot supply final numeric evidence or change configured thresholds.

Ollama is disabled by default. If enabled, objectives and schema metadata leave the worker process for the configured endpoint. Keep the endpoint trusted and local if local-only processing is required. The setting is server-controlled, never a per-request arbitrary URL. Environment proxy variables are disabled for model requests and redirects are not followed.

## Remaining operational requirements

- No MFA, SSO, password recovery email, account self-registration or external service-account keys. Use your reverse proxy/identity gateway if those are required, and review session interactions.
- No encryption-at-rest implementation in application code. Use encrypted disks/database storage and encrypted, access-controlled backups.
- Audit events are not cryptographically tamper-proof. Export them to immutable storage if needed; administrators with database credentials can change them.
- Production gives API/workers a separate runtime role with table DML and sequence access. Schema creation and role administration remain with the migration/bootstrap account, whose connection secret is mounted only into the migration service. Rotate both credentials through an operator-controlled maintenance window.
- Quotas cover registered active source files, not report/database growth, temporary concurrent uploads or watcher archives. Monitor disk use and apply host-level resource/storage budgets.
- No distributed storage backend, multi-region failover, tenant organization sharing, antivirus service, forensic chain-of-custody certification or regulated-data compliance certification is claimed.
- IP-rate limiting depends on correct proxy trust. The standalone production configuration publishes only Caddy and trusts its explicit private address. Keep that subnet isolated from untrusted containers.
- PDF built-in fonts are Latin-1. Use JSON/Markdown for lossless Unicode, or package reviewed Unicode fonts before relying on localized PDF output.

## Verification and incident response

Run `python -m pip_audit -r requirements.lock --require-hashes` regularly and update lockfiles after review. Scan container OS packages and pin approved image digests. Test dependency updates, migrations and browser behavior before promotion. The delivery audit is a point-in-time check, not a promise that future vulnerabilities will not be found.

If credentials may be compromised: disable affected accounts, revoke their sessions, rotate passwords and relevant infrastructure secrets, preserve audit/log evidence, and review dataset/report access. If a worker is unstable: stop accepting uploads, retain source files, inspect resource limits and failure reasons, then reproduce on an isolated test environment.

No private vulnerability-reporting destination is configured because this is a user-owned project. Set an appropriate private reporting channel before publishing it publicly.
