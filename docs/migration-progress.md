# Python migration checklist

- [x] Ground the existing runtime and contract behavior.
- [x] Use src/marketplace with hybrid feature-based layers, shared infrastructure, and unit/integration tests.
- [x] Replace TypeScript tooling and process entry points with Python.
- [x] Install local dependencies and generate `uv.lock`.
- [x] Preserve role configuration, liveness behavior, and secret redaction.
- [x] Add SQLAlchemy catalog projections and Alembic migrations.
- [x] Complete local checks, migration exercise, and real HTTP smoke test.
- [x] Add merchant login/session/lockout, TokenSigner port, migration, and OpenAPI/Postman checks (#177 selected scope).
- [x] Verify auth and migrations against local PostgreSQL 17.11 (31 tests and Newman 25 requests / 103 assertions).
- [x] Integrate ES256 JWT signing from a mounted PEM, issuer/audience configuration, and public JWKS (#182).
- [ ] Complete signing-key rotation (#184), refresh/logout (#178/#179), customer auth, and wallet authorization.
- [ ] Resolve the deployment, signing, queue, and indexer decisions listed in architecture.md.
