# Verification — 1.1.0 — 23 September 2026

## Status

The hardened application has been integrated into [pull request #1](https://github.com/Rohan-SDE/Agentic_Data_Parsing_Engine/pull/1). GitHub CI now executes the infrastructure checks that were unavailable in the original local workspace. The authoritative result is the latest `release-gate` on the PR; do not infer approval from the historical local results below.

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

## CI gates and deployment acceptance

| Gate | Supplied executable check / remaining input |
|---|---|
| PostgreSQL concurrency | Passed in GitHub CI: five tests covering exclusive claims, queue/storage quotas, rate limiting and idempotency under concurrent submissions |
| Container and production profile | Executed by GitHub CI on a disposable Docker runner; inspect the latest containers job for the exact candidate image build |
| Runtime database privileges | Production acceptance has verified that API credentials cannot administer roles/databases or create schema objects |
| HTTPS and matched restore | Production acceptance has exercised Caddy with a verified local CA, checks secure cookies/docs restrictions, restarts processes, performs a matched backup/restore and verifies source checksums and retained reports |
| Container vulnerabilities | CI scans the actual rebuilt app, PostgreSQL and Caddy images; high/critical findings fail the gate, with no vulnerability suppressions |
| Real browser | Passed in GitHub CI: Chromium login, upload, thresholds, real worker/report, PDF download, dashboard/mobile overflow, administration, logout and no JavaScript errors; desktop/mobile captures are retained |
| Public deployment | Needs the destination server, domain/DNS and credentials configured by its operator; public ACME, firewall, disk alarms and monitoring must be verified there |
| Capacity and disaster recovery | Representative workload measurements, encrypted off-host backup schedule and a separate-host recovery drill remain deployment-specific |
| Optional Ollama | Live model and outage check if enabled; no model/server is bundled |

The GitHub workflow has a final `release-gate` that fails unless every required CI job succeeds. It retains reports and failure evidence. Merely including the workflow does not satisfy these gates. The actual approved images must be recorded and pinned by digest after successful validation.

## Scope and provenance

This is the clean implementation based on the project's previously read specification, updated from the saved 1.0 source. It targets a single Linux host with persistent volumes and multiple workers. It does not claim multi-region availability, compliance certification, or unrestricted autonomous code execution.

The archive excludes secrets, private data, runtime databases, virtual environments, browser binaries and model weights. `MANIFEST.sha256` records the packaged source and evidence. Follow the deployment and operations guides, including the existing-database role-provisioning step when upgrading an earlier deployment.

## CI corrections

- Scoped the browser login selectors to the login form; the account-creation form also has a Username label.
- Replaced the Debian-based runtime images after high/critical OS-package findings.
- Rebuilt Caddy 2.11.4 with Go 1.26.8 and compatible patched crypto/network/text/gRPC dependencies.
- Rebuilt PostgreSQL's gosu helper from its pinned 1.19 source commit with Go 1.26.8, retaining the official privilege-drop entrypoint.
- Pinned CI actions to the reviewed commit SHAs used by the successful runs and added readable package-level image-scan diagnostics.

The earlier functional CI run [35883990867](https://github.com/Rohan-SDE/Agentic_Data_Parsing_Engine/actions/runs/35883990867) passed 84 application tests, all five PostgreSQL tests, browser acceptance and production/recovery integration. Its image-security jobs correctly failed and prompted the image rebuilds above; that run was not a release approval.

The rebuilt images and all validation jobs passed on commit `e113f3098e501f7cd3e193fd0a82173ecd652180` in [run 35884842098](https://github.com/Rohan-SDE/Agentic_Data_Parsing_Engine/actions/runs/35884842098): 89 tests, real Chromium, development/production containers and recovery, and all three high/critical image scans. The aggregate job's inherited working directory was then corrected to use the runner's temporary directory, since that job does not check out source. The final PR status includes a fresh full run after this correction.

This evidence establishes automated acceptance of the project. A live production deployment still needs the intended domain/server, host workload sizing, disk monitoring, encrypted off-host backups and operational ownership described in DEPLOYMENT.md.
