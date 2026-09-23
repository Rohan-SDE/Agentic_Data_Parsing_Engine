# Changes

## 1.1.0 — 2026-09-23

- Correct the development Compose PostgreSQL hostname.
- Add owner-scoped, durable job idempotency keys with replay/conflict behavior and migration 0002.
- Preserve dashboard submission keys across failed network submissions.
- Bound total request-body duration, DB connection/pool/statement waits and simultaneous password hashing.
- Recheck disabled accounts after uploads and before new jobs are committed.
- Fail production startup/readiness on a stale database schema.
- Tie each worker health check to its own process identity and heartbeat; clean up identity on shutdown.
- Replace the production overlay with a standalone configuration: no API host port, explicit proxy trust, file-backed secrets, constrained containers and separated runtime/migration database roles.
- Generate private production settings without displaying credentials or overwriting existing settings.
- Add daily maintenance and read-only checksum verification.
- Add matched, streamed DB/file backups, resume writers on failure, and restore only into new projects after checksum/archive validation.
- Add regression and PostgreSQL concurrency tests; require functional, database, container, HTTPS/recovery, vulnerability and browser CI gates, retaining their evidence.
- Update deployment, recovery, API and security documentation.

These are implementation changes. Consult docs/VERIFICATION.md for checks actually run and deployment gates still pending.
