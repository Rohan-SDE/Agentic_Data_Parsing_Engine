# Agentic Data Parsing Engine

A complete, local-first data analysis application for Rohan Roy's Agentic Data Parsing Engine project. Upload a dataset or drop it into a watched folder, set an investigation objective, and receive a traceable diagnostic report.

**Release:** 1.1.0 hardened production candidate. The application and safeguards are implemented; host-specific production acceptance is still required. See [VERIFICATION.md](docs/VERIFICATION.md) for actual test results and unverified gates. This is a clean implementation from the current project specification, not a patch to the older source archive.

## What is included

- Responsive browser dashboard: sign-in, datasets, uploads, analysis runs, findings, evidence, exports, account administration and audit activity.
- FastAPI API, PostgreSQL production database, Alembic migrations; SQLite for local development.
- CSV, TSV, flat JSON, JSONL/NDJSON and key-value telemetry log ingestion.
- Streaming exact row/quality/numeric aggregates, deterministic reservoir sampling, statistics, correlations, robust outlier checks and explicit engineering thresholds.
- Optional local Ollama planning with a strict five-tool allowlist, bounded responses and deterministic fallback. No generated code execution.
- Durable job submission idempotency, bounded request/DB waits, schema startup checks and per-worker health.
- Separate job worker with process isolation, resource limits, leases, crash recovery, bounded delayed retries, cancellation and stale-result fencing.
- Stable-file folder watcher with startup backlog discovery and duplicate suppression.
- Downloadable JSON, Markdown and PDF reports with source SHA-256 and analysis scope.
- Argon2id passwords, revocable server-side sessions, CSRF/origin protections, owner-only data access, database-backed rate limits, upload/queue/storage quotas, security headers and audit events.
- Automated matched backups, safe restores into new projects, source checksum verification and scheduled cleanup.
- Hash-locked dependencies, Docker Compose, a standalone HTTPS production configuration, CI, automated tests, examples and operational runbooks.

## Start on Windows (Python 3.12)

Extract the ZIP and open PowerShell inside `agentic-data-parsing-engine`.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m engine.cli migrate
.\.venv\Scripts\python.exe -m engine.cli create-user rohan --admin
```

Choose your own password when prompted (12–128 characters). There is no default password. If `.env` already exists, keep it and review its settings instead of overwriting it.

**Terminal 1 — API and dashboard:**

```powershell
.\.venv\Scripts\python.exe -m engine.cli serve
```

**Terminal 2 — analysis worker:**

```powershell
.\.venv\Scripts\python.exe -m engine.cli worker
```

Open **http://localhost:8000** and sign in. Both processes must remain running. `scripts/setup.ps1`, `scripts/start-api.ps1` and `scripts/start-worker.ps1` provide the same workflow if your local PowerShell policy permits scripts. The commands above do not require changing that policy.

Python 3.12 is the deployment target. Metadata permits 3.13, but that version has not been verified in this delivery. Python 3.14 is excluded.

## Start on Linux or macOS

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
cp .env.example .env
python -m engine.cli migrate
python -m engine.cli create-user rohan --admin
python -m engine.cli serve
```

In another terminal, activate the same environment and run `python -m engine.cli worker`. Run all commands from the repository root.

## Try the included telemetry dataset

1. Open **Datasets**, then upload `examples/drone_telemetry.csv`.
2. Use the objective: `Inspect motor temperature and battery voltage for unusual measurements.`
3. Expand **Set measurement limits** and enter:

   ```json
   {"motor_temperature":{"max":80},"battery_voltage":{"min":10}}
   ```

4. Run the analysis. The synthetic fixture contains 600 records, nine high-temperature readings, two low-voltage readings and ten missing altitude values.
5. Inspect the evidence and tool trace, then download the report. The example thresholds demonstrate functionality; use limits validated for your actual equipment.

## Docker with PostgreSQL

Requires Docker Engine/Desktop with Compose v2. Add `POSTGRES_PASSWORD` to `.env`, using a randomly generated **hexadecimal** value (URL-safe; do not use the literal example name):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Put the printed value after `POSTGRES_PASSWORD=` in `.env`, then:

```bash
docker compose up --build -d
docker compose exec api python -m engine.cli create-user rohan --admin
```

Open http://localhost:8000. PostgreSQL is internal to the Compose network. Application files and database state use separate named volumes. **Do not run `docker compose down -v` against real data.**

For an internet-facing host, use [DEPLOYMENT.md](docs/DEPLOYMENT.md), including its HTTPS configuration and release gates. Development mode is not the production deployment profile.

## Optional Ollama

Install Ollama and download a model on the machine that will run inference:

```bash
ollama pull llama3.2:3b
```

Set in `.env` for a local installation:

```dotenv
ADPE_OLLAMA_ENABLED=true
ADPE_OLLAMA_URL=http://127.0.0.1:11434
ADPE_OLLAMA_MODEL=llama3.2:3b
```

Restart the worker. For Docker on Desktop, set `ADPE_OLLAMA_URL=http://host.docker.internal:11434`. On Linux, the host Ollama service must listen on a host interface reachable from the Compose network; restrict it with your firewall, or run it on a private internal service network. Do not expose unauthenticated Ollama publicly.

Only the objective, column names/types and record count go to the configured model endpoint. Raw rows are not sent. Local privacy depends on using a trusted local endpoint; pointing the setting to another host sends that metadata to that host. Model errors do not stop deterministic analysis and are visible in the report. No Ollama server/model is bundled in the archive.

## Watch a folder

Set `ADPE_WATCHER_USER=rohan` in `.env`, restart processes that need the setting, then run:

```bash
python -m engine.cli watch
```

Place files in `data/incoming/`. For reliable handoff, write `filename.csv.part`, close it, then atomically rename it to `filename.csv` in the same folder. Keep the folder operator-owned; do not allow untrusted local processes to mutate files during ingestion.

- Files must be unchanged for 10 seconds by default.
- Accepted originals move to `data/processed/` after the dataset and job are persisted. **Processed means ingested, not analysis completed**; check the run status.
- Unsupported/oversized inputs and quota rejections move to `data/rejected/`. Syntax errors discovered during parsing produce a failed job.
- Identical content with the same filename and owner reuses an existing dataset/job. For a deliberate rerun, use the dashboard.
- Docker watcher: `docker compose --profile watcher up -d`; use `docker compose cp your.csv watcher:/data/incoming/your.csv.part`, then rename it inside the container once the copy finishes.
- Keep one watcher per shared data volume. A filesystem lock prevents duplicate watcher processes on one host.

## Analysis guarantees and boundaries

| Measurement | Scope |
|---|---|
| Record count, schema, missing fields | All records |
| Numeric count, min/max, mean, sample standard deviation | All finite numeric values with absolute value ≤ 1e100 |
| Configured threshold breaches | All numeric values; at most ten evidence rows per field |
| Statistical anomalies, median | Bounded reproducible sample |
| Pearson correlations | Sample; first 32 numeric columns with at least ten numeric observations |
| Duplicate check | Normalized sample; strings truncated to 256 characters |

The sample cap is 5,000 records by default and becomes smaller for wide datasets to bound memory. Row numbers are one-based data-record numbers excluding CSV headers and blank lines. Outlier detection uses modified z-scores (threshold 3.5); a constant-median baseline uses a 5% relative tolerance (minimum 1e-9). These are generic heuristics, not domain-calibrated probabilities. A finding's `confidence` is evidence strength. Correlation does not prove causation.

Default limits: 64 MiB per upload, 1 GiB active dataset storage per account, two million records, 128 columns, 16,384 characters per field, 300 seconds per job and ten active jobs per account. JSON arrays/objects are capped at 8 MiB; use JSONL for larger JSON data. Inputs must be UTF-8 and flat. ZIP, executable files, binary telemetry, Excel, PDF ingestion, automatic OCR and arbitrary Python execution are not supported. Source data is never silently cleaned or overwritten.

The planner makes one bounded tool-selection decision. Mandatory schema/quality checks and numeric anomaly checks cannot be suppressed. Each selected tool runs once; failures are isolated and yield a visibly partial report. It is not an unrestricted autonomous agent.

Linux workers enforce process address-space and CPU limits plus a parent-enforced wall-clock limit. Windows/macOS local development enforces the wall-clock limit; use the Linux container profile for production resource enforcement. API replicas and workers must share the same application volume. Multi-host deployments need a separately designed shared/object-storage backend; this release targets one host with multiple worker processes.

## API and developer workflow

Local API reference: http://localhost:8000/docs. OpenAPI schema: `/openapi.json`. Both are disabled in production. The reference is self-hosted and does not use external JavaScript/CDNs. See [API.md](docs/API.md) for a complete cookie/CSRF client example.

```bash
python -m pip install --require-hashes -r requirements-dev.lock
python -m pytest --cov=engine --cov-report=term-missing
python -m ruff check .
python -m pip_audit -r requirements.lock --require-hashes
python scripts/benchmark.py --rows 100000
```

Optional browser acceptance test:

```bash
python -m pip install playwright==1.58.0
python -m playwright install chromium
python scripts/browser_smoke.py
```

CI also includes real PostgreSQL concurrency tests and a Docker integration run. For local PostgreSQL tests, set `ADPE_TEST_POSTGRES_URL` to a **disposable database**; that suite truncates its tables. Never point it at a real deployment.

## Project map

```text
engine/           API, security, parsers, tools, worker, watcher, reports, CLI
engine/static/    Dashboard and self-hosted API reference
alembic/          Database migrations
tests/            Functional, security, worker and PostgreSQL tests
scripts/          Windows launchers, browser and deployment acceptance tests
examples/         Synthetic CSV, JSONL and telemetry logs
deploy/           HTTPS proxy configuration
docs/             Architecture, API, deployment, operations, security, verification
```

Read [OPERATIONS.md](docs/OPERATIONS.md) for backups, restore, password reset, cleanup and troubleshooting. Read [SECURITY.md](docs/SECURITY.md) before allowing access outside your machine.
