# Agentic Data Parsing Engine

The hardened 1.1 application lives in **[production/](production/README.md)**. It includes the dashboard, authenticated API, durable PostgreSQL jobs, bounded agent planning, reports, deployment configuration, recovery tools and automated release gates.

Start with [installation instructions](production/README.md), [production deployment](production/docs/DEPLOYMENT.md), and [actual verification status](production/docs/VERIFICATION.md). Run application commands from the `production` directory.

```bash
cd production
python -m pip install --require-hashes -r requirements.lock
python -m engine.cli migrate
python -m engine.cli create-user rohan --admin
python -m engine.cli serve
# In another terminal in production/: python -m engine.cli worker
```

Local development uses SQLite; production requires the documented PostgreSQL/HTTPS configuration. There is no default account or password.

## Repository layout

- `production/`: the maintained release candidate, its tests and operational documentation.
- `.github/workflows/ci.yml`: release gates for this application, including PostgreSQL concurrency, containers, HTTPS/recovery, browser acceptance and image audits.
- `agentic-parsing-engine/` and `agentic-parsing-frontend/`: preserved earlier prototype. These use different API contracts and are not part of the production image or release approval. Do not expose their unauthenticated demo endpoints as a production service.

The hardened release is a separate implementation. Existing prototype reports and uploaded files are preserved in the old folders; no automatic data migration or API compatibility is claimed. Re-upload approved source files into an authenticated account when moving to the maintained application.

Production readiness requires successful release gates and target-host acceptance. A workflow definition or local unit-test result alone does not establish that status.
