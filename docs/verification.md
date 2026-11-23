# Python migration verification

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
