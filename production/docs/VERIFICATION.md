# Verification — 1.1.0 — 23 September 2026

## Status

The hardened source and automated release gates are delivered. **Production approval remains pending external CI and target-host acceptance.** The hardened release is being integrated into `Rohan-SDE/Agentic_Data_Parsing_Engine` on a review branch. External CI and target-host acceptance are pending; no successful CI run or live deployment is claimed yet.

## Executed against this revision

| Check | Result |
|---|---|
| Python | 3.12.14 |
| Automated suite | **84 passed, 5 PostgreSQL tests skipped**; full output in `evidence/pytest.txt` |
| Coverage | Approximately 79% in the main pytest process; child-process execution is not included |
| Syntax/lint | Ruff, Python compile checks and JavaScript syntax checks passed |
| Dashboard integration | JSDOM with the real HTTP API and worker passed login, raw upload, thresholds, report rendering, filename XSS escaping, PDF export, administration and logout, with no script errors |
| Dependency audit | **34 runtime dependencies checked; zero known vulnerabilities reported** at verification time |
| Migrations | SQLite schema upgrade, drift detection, downgrade and re-upgrade passed, including revision 0002 |
| New runtime regressions | Submission replay/conflict, key validation, request deadline, stale-schema readiness, per-worker health, bounded hashing, disabled-account recheck, cleanup retention and source-corruption detection passed |
| Recovery regressions | Manifest integrity, traversal/link rejection, existing-resource refusal, writer restart after failed dump and verification-before-start ordering passed using controlled command doubles |
| Production configuration | YAML parsing and invariants passed: no API host port; separate runtime/migration secrets; explicit proxy trust; maintenance configured. This is not a Compose runtime test. |
| Existing functional checks | Parsers, exact aggregates, samples, allowlisted model contracts/fallback, quotas, owner/role isolation, session/CSRF protections, real child analysis, leases, retry/cancellation fencing, deadline termination and exports passed |

Tests use synthetic data. Dependency findings are point-in-time results and do not include container operating-system packages. A Starlette TestClient deprecation warning remains; assertions pass.

The earlier 1.0 verification also exercised hash-locked Windows dependency resolution and visually inspected a generated PDF. Those are historical evidence, not new native Windows or browser runs for 1.1.

## Required gates still pending

| Gate | Supplied executable check / remaining input |
|---|---|
| PostgreSQL concurrency | Five tests: exclusive claims, queue/storage quotas, rate limiting and idempotency under concurrent submissions; CI provides a disposable PostgreSQL service |
| Container and production profile | `scripts/compose_smoke.py` and `scripts/production_smoke.py`; requires a disposable Docker runner |
| Runtime database privileges | Production acceptance verifies that API credentials cannot administer roles/databases or create schema objects |
| HTTPS and matched restore | Production acceptance uses Caddy with a verified local CA, checks secure cookies/docs restrictions, restarts processes, performs a matched backup/restore and verifies source checksums and retained reports |
| Container vulnerabilities | CI scans app, PostgreSQL and Caddy images; high/critical findings fail the gate |
| Real browser | `scripts/browser_smoke.py` runs Chromium and captures desktop/mobile evidence. JSDOM does not prove layout, CSP enforcement or real browser behavior |
| Public deployment | Needs the destination server, domain/DNS and credentials configured by its operator; public ACME, firewall, disk alarms and monitoring must be verified there |
| Capacity and disaster recovery | Representative workload measurements, encrypted off-host backup schedule and a separate-host recovery drill remain deployment-specific |
| Optional Ollama | Live model and outage check if enabled; no model/server is bundled |

The GitHub workflow has a final `release-gate` that fails unless every required CI job succeeds. It retains reports and failure evidence. Merely including the workflow does not satisfy these gates. The actual approved images must be recorded and pinned by digest after successful validation.

## Scope and provenance

This is the clean implementation based on the project's previously read specification, updated from the saved 1.0 source. It targets a single Linux host with persistent volumes and multiple workers. It does not claim multi-region availability, compliance certification, or unrestricted autonomous code execution.

The archive excludes secrets, private data, runtime databases, virtual environments, browser binaries and model weights. `MANIFEST.sha256` records the packaged source and evidence. Follow the deployment and operations guides, including the existing-database role-provisioning step when upgrading an earlier deployment.
