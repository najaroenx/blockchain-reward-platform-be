# Python migration verification

## Current issue split

After splitting the work by issue, the final suite passed 149 tests and PostgreSQL passed 78
auth/migration tests. Each preceding branch also passed its complete checks: schema foundation 41,
schema acceptance 53, login 75, and signer 112 tests. Three new T6/T7 cases account for the increase
from the original 146-test snapshot. The final PostgreSQL Newman run passed 36 requests and 147
assertions. Runtime source and API artifacts match the original snapshot byte for byte.

See [issue ownership](auth-work-ownership.md) for branches, PR bases, and preserved archive refs.
The sections below record the original implementation runs before the ownership split.

## Original refresh rotation on BE-0307

Verified locally on 2026-11-13:

- Full suite: 146 passed. Ruff, formatting, strict mypy for 50 source files, and all six import
  boundaries passed. OpenAPI and Postman artifacts validate and match the runtime.
- Live PostgreSQL auth/migration suite: 75 passed. It includes 20 concurrent refreshes of one token,
  replay while a descendant signs, rollback, all T9–T14 checks, and direct database owner guards.
- T14 migration tests cover old disabled-owner data, downgrade/re-upgrade, and 20 session inserts
  racing account disable. PostgreSQL and SQLite both leave no unrevoked sessions for that owner.
- Newman passed 36 requests / 147 assertions on SQLite and PostgreSQL. The verifier checked three
  ES256 JWTs against HTTP JWKS, inspected family revocation, and confirmed signer failure left its
  parent session unused. Replay cases disable Newman's automatic cookie jar to send the intended token.
- Disposable databases were removed after verification. No shared staging deployment or migration
  of an existing local development database is claimed.
- One simultaneous local verification run reported PostgreSQL connection timeouts and a SQLite
  concurrent-login 503. The affected login test passed in isolation; sequential reruns passed all
  146 tests and all 75 PostgreSQL checks without changing application timeouts.

See [refresh behavior](auth-refresh.md) for source differences, migration behavior, and commands.

## ES256 signing on BE-0393

Verified locally on 2026-11-13 with Python 3.12.11:

- Ruff lint/format, strict mypy for 45 source files, and all six import boundaries passed.
- Full pytest suite: 109 passed. New checks cover ES256 signatures, HTTP JWKS, issuer/audience,
  expiry, tampering, alternate algorithms, invalid PEMs, sanitized errors, and isolated role config.
- Live PostgreSQL auth/migration suite: 38 passed, including 20 concurrent logins with real JWTs.
- Newman on SQLite and PostgreSQL: 26 requests / 107 assertions passed on each. The verifier decoded the
  successful JWT using the public HTTP JWKS and configured issuer/audience.
- OpenAPI validation and generated Postman artifact checks passed. Test PEMs are generated in
  temporary directories and removed after use; production secrets have not been provisioned.

See [ES256 signing](jwt-signing.md) for configuration, source reconciliation, and rotation limits.

## Merchant login on BE-0306

Verified locally on 2026-11-12 with Python 3.12.11:

- Ruff lint/format, strict mypy for 44 source files, and all six import boundaries passed.
- Full pytest suite: 72 passed. Auth checks include concurrent login/lockout, uniform credential
  failures, signer failure, account changes during signing, and database rollback without a session.
- Alembic upgrade/downgrade/re-upgrade and schema drift checks passed on temporary SQLite databases
  and a live PostgreSQL 17.11 Compose container (`postgres:17-alpine`). The PostgreSQL auth/migration
  run passed 31 tests, including T1/T2/T3/T4/T5/T8 storage invariants and concurrent login/lockout.
- Generated login OpenAPI passed schema validation and matches the runtime; Postman artifacts match.
- Newman against isolated seeded SQLite and PostgreSQL HTTP apps passed 25 requests and 103 assertions each. The fixture uses
  real Argon2 verification and an opaque test signer. At that revision the default signer was
  unavailable pending #182, returning 503 without creating a session or cookie.
- PostgreSQL databases are generated per test/run and removed afterward. The Compose container
  remains running on loopback port 5432. Refresh/logout API flows and a shared staging deployment
  remain unverified; T2/T3 evidence here is at the database/repository layer.

See [merchant login](auth-login.md) for scope, source discrepancies, and repeatable commands.

## Original Python foundation

Verified locally on 2026-11-10 using Python 3.12.11 on branch `recreate/python-backend`.

| Check | Result |
| --- | --- |
| Dependency installation | Installed in `.venv`; resolution recorded in `uv.lock`; frozen/offline lock check passed. |
| Ruff lint and format | Passed. |
| Strict mypy | Passed for all 28 source files. |
| Import boundaries | All 6 hybrid feature-based architecture contracts passed. |
| pytest | 41 passed, no failures, skips, or warnings. |
| SQLite migrations | Upgrade, repeated upgrade, schema drift check, downgrade, and re-upgrade passed against temporary databases. |
| PostgreSQL dialect | psycopg driver loads and Alembic generates PostgreSQL DDL successfully. A real server connection has not been tested. |
| HTTP process | Started the installed `reward-api` entry point on loopback. `/healthz`, `/readyz`, and `/openapi.json` returned 200. Catalog without an indexed checkpoint returned the intended 503. Shutdown completed its lifespan cleanup. |
| Worker and indexer | Both `--check` commands passed; normal execution exits with a clear disabled message. No transaction or indexing work was claimed. |
| Contract reference | All 5 bundled ABI hashes match the manifest and the pinned Git commit. ABIs also matched the previously freshly compiled artifacts of that contract revision. |
| Legacy SQL | Archived file matches the original bytes. |
| Git whitespace check | Passed. |

The package uses hybrid feature-based organization under `src/marketplace/listings/` with domain,
application, infrastructure, and presentation inside the feature. Shared connections and ABI assets
remain in `src/marketplace/infrastructure/`. Listings integration tests are in
`tests/integration/listings/`. All 41 tests passed after migrating imports and Alembic model discovery. Coupons has four package
skeletons and no registered endpoint; the six import-boundary rules cover both features.

Run again with:

```bash
.venv/bin/python scripts/verify.py
.venv/bin/python scripts/sync_contract_reference.py --check
UV_CACHE_DIR=/private/tmp/reward-uv-lock-check .tools/uv/bin/uv lock --check --offline
.venv/bin/alembic check
```

The installed Alembic and API entry points ran from outside the repository with a fresh SQLite
database. The current layout's HTTP results and shutdown evidence are saved in
`/private/tmp/marketplace-hybrid-smoke-r35t08md/summary.json` and
`/private/tmp/marketplace-hybrid-smoke-r35t08md/api.log`. Those are temporary verification evidence.

Docker CLI is present, but its daemon socket does not exist on this machine. Consequently the
Compose PostgreSQL service and real PostgreSQL integration remain unverified. The default SQLite
environment is working. No live chain, KMS, queue, authentication provider, reorg handling, or
transaction submission has been tested or represented as implemented.
