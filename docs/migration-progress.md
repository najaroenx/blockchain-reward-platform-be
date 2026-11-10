# Python migration checklist

- [x] Ground the existing runtime and contract behavior.
- [x] Use src/marketplace with hybrid feature-based layers, shared infrastructure, and unit/integration tests.
- [x] Replace TypeScript tooling and process entry points with Python.
- [x] Install local dependencies and generate `uv.lock`.
- [x] Preserve role configuration, liveness behavior, and secret redaction.
- [x] Add SQLAlchemy catalog projections and Alembic migrations.
- [x] Complete local checks, migration exercise, and real HTTP smoke test.
- [ ] Confirm PostgreSQL integration against a running PostgreSQL server.
- [ ] Resolve the deployment, authentication, signing, queue, and indexer decisions listed in architecture.md.
