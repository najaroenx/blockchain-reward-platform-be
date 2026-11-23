# Python backend architecture

Status: Python foundation, read-only catalog, and merchant login/session/lockout implemented.
JWT signing uses an ES256 adapter behind the TokenSigner port. Live chain integration is not enabled.

## Existing behavior and migration scope

The TypeScript baseline at `0e5e083` contained an HTTP health endpoint, role-specific environment
validation, redacted logging, and worker/indexer processes that logged placeholder heartbeats.
It did not connect to PostgreSQL, a queue, KMS, or an EVM node. There were no implemented marketplace
transaction endpoints to port. The Python migration replaces those runtime files and their tests.

The old Prisma SQL was a combined reference for fourteen proposed features, rather than an applied,
verified marketplace schema. It is preserved verbatim in `docs/legacy/0001_typescript_reference.sql`
and is excluded from Alembic. This is a fresh Python database baseline, not a production data migration.
Do not point it at an existing database without a separate migration review.

## Chosen structure: src layout with hybrid feature-based organization

See [Layer-based versus Hybrid Feature-based](layer-based-vs-feature-based.md) for the comparison
and shared-infrastructure ownership rules.

```text
src/marketplace/
  main.py                          composition root, HTTP app, lifespan, error mapping
  settings.py                      role-specific configuration
  logging_config.py                JSON logging and secret redaction
  worker.py                        worker entry point; execution disabled
  indexer.py                       indexer entry point; execution disabled
  listings/
    domain/entities.py             immutable listing and marketplace values
    application/use_cases.py       catalog availability and not-found behavior
    application/ports.py           repository interface consumed by use cases
    application/dto.py             query result values
    application/exceptions.py      application failures
    infrastructure/models.py       listings-owned SQLAlchemy projections
    infrastructure/repository.py   listings-owned query adapter
    presentation/router.py         HTTP endpoints receiving an injected provider
    presentation/schemas.py        HTTP validation and response models
  coupons/
    domain/                        package skeleton for coupon entities and rules
    application/                   package skeleton for coupon use cases and ports
    infrastructure/                package skeleton for coupon adapters
    presentation/                  package skeleton for coupon HTTP routes and schemas
  auth/
    domain/entities.py             login policy and identity/session values
    application/use_cases.py       credential verification and login coordination
    application/ports.py           LoginStore, PasswordVerifier, TokenSigner
    infrastructure/models.py       seven FEAT-03 foundation tables
    infrastructure/repository.py   rate reservations, lockout, atomic sessions
    infrastructure/passwords.py    Argon2id and equivalent-cost dummy verification
    presentation/router.py         login route and secure refresh cookie
  infrastructure/
    database.py                    shared engine and request-scoped sessions
    blockchain/abi/                pinned reference, not a deployment binding
migrations/                        Alembic schema owned by this backend
tests/conftest.py                  shared fixtures
tests/unit/                        configuration, logging, and reference tests
tests/integration/listings/         catalog HTTP and database tests
tests/integration/                 shared migration and process tests
scripts/verify.py                  repeatable verification entry point
scripts/sync_contract_reference.py reproducible ABI extraction from the pinned Git commit
```

The installable package is `marketplace` under `src/`. Organize by feature first, then by domain,
application, infrastructure, and presentation within each feature. Listings owns the existing
catalog read use cases, projection models, repository, and HTTP endpoints. Coupons is an explicitly
requested feature skeleton: each layer is a Python package, with no new use case, ORM model, or
HTTP route. Coupon creation and minting still require the deployment and signing decisions.

Shared connection management and ABI reference assets stay in the package-level infrastructure.
Feature infrastructure owns its ORM models and queries. Shared infrastructure does not import
features. Database migrations explicitly register the implemented feature models.

The system remains a modular monolith. CLI entry points are `reward-api`, `reward-worker`, and
`reward-indexer`, targeting `marketplace.main`, `marketplace.worker`, and `marketplace.indexer`.
Configuration, scripts, migrations, and tests stay at the repository root.

Within listings, domain objects describe listing values. Query DTOs and availability errors belong
to application. Use cases depend on domain and repository protocols. Presentation handles HTTP.
`main.py` wires the concrete repository into the use case and injects its provider into the router.

Import Linter covers listings, coupons, and auth with six dependency rules: domain independence, application independence,
presentation isolation from persistence, feature infrastructure isolation from HTTP, settings/logging
independence from features, and shared infrastructure independence from features. Domain and
application do not depend on FastAPI, Pydantic, or SQLAlchemy.

## Responsibilities

The auth feature owns merchant login, session storage, and lockout. `main.py` wires a SQL repository
and password verifier into `Login`. The configured signer loads a mounted P-256 private PEM at
startup and publishes its public JWK at `/.well-known/jwks.json`. Development/test without signer
configuration fails with AUTH_UNAVAILABLE and creates no session. Production requires complete
signer configuration. No mock signer is used by the normal API entry point.

Migration `0002_auth_login` adds the FEAT-03 identity/session foundation because the Python base
did not contain those tables. It does not execute the archived SQL. Customer OTP, merchant
onboarding, logout/session management, JWT key rotation, and shared-schema reconciliation retain
their separate owners. See [merchant login](auth-login.md) for source discrepancies and acceptance evidence.

Rate reservations commit hashed PENDING attempts before password work and signing. Success then
commits the session, completed attempt, last-login update, and auth event atomically. Signing runs
outside the transaction; failures create no session. The repository rechecks account credentials
and identity bindings before commit. Countable-failure flags reset under the identifier lock,
preserving audit history without relying on timestamps to order a reset.

Auth PostgreSQL writes use READ COMMITTED with transaction advisory locks for IP and identifier
scopes. Local SQLite writes use BEGIN IMMEDIATE and enforce foreign keys. The server ignores
forwarded headers by default; proxy trust remains a deployment decision.

Refresh rotation uses a family advisory lock and the unique parent index, with signing outside
transactions and fresh account/session checks before commit. Replay revocation commits before the
401 response and blocks pending descendants from extending the family. Migration `0003_disabled_sessions`
revokes sessions atomically when either owner type is disabled and guards raw session writes.
See [refresh behavior](auth-refresh.md) for cookie/error contracts and migration details.

Contracts own trading authorization on chain, balances, escrow, coupon burns, and settlement.
The backend implements merchant login and will own wallet proof, request idempotency, transaction
tracking, and projections after those policies are selected. Backend login alone does not authorize
acting as a wallet. Changing the RPC transaction's signer changes `msg.sender` in the contract.

The implemented catalog reads projections only. Each query is scoped by chain ID and marketplace
address. Large EVM integers are stored as exact decimal strings and returned as strings. A missing
checkpoint yields HTTP 503, rather than pretending an unindexed marketplace has no listings.
The returned `indexed_block` describes projection progress; it does not claim the chain is current.
No public endpoint writes listings, submits transactions, mints coupons, or releases escrow.

SQLite is a local development/test adapter. PostgreSQL with psycopg is required in production.
Database sessions belong to one request and close afterward. Alembic owns schema changes; the
application never creates or migrates tables on startup. Worker and indexer configuration checks
remain separate entry points and refuse to run a pretend processing loop.

Implementation references: [FastAPI routers and dependencies](https://fastapi.tiangolo.com/tutorial/bigger-applications/),
[SQLAlchemy sessions](https://docs.sqlalchemy.org/en/20/orm/session_basics.html),
[Alembic migrations](https://alembic.sqlalchemy.org/en/latest/tutorial.html), and
[uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/).
Tests use [Starlette's supported HTTPX2 test client](https://www.starlette.io/testclient/).

## Decisions still required

| Decision | Why it is needed before the dependent feature |
| --- | --- |
| Intended deployment | Confirm chain ID, RPC, deployment block, Coupon/Vault/THB/proxy addresses, implementation address, and implementation revision/runtime code. The vendored ABI commit is only a candidate. |
| Wallet custody and signing | Decide user-signed transactions versus worker custody/KMS, how users prove wallet ownership, and which operations may use `redeemFrom`. |
| Authentication | Provision the mounted ES256 PEM through deployment secrets, complete key rotation (#184), customer OTP, account roles, revocation APIs, and wallet-to-user authorization. Merchant login/session/lockout and JWT signing are implemented. |
| Points | Confirm the deployed ERC-20 addresses and precision. Contract source accepts ERC-20 payments, not a backend-only points balance or native ETH. |
| Queue and operations | Select queue/storage, retries, idempotency scope, nonce ownership, replacement policy, and receipt/finality tracking before implementing a transaction worker. |
| Indexing and reorgs | Select start block, finality policy, replay/reorg rollback, checkpoint ownership, and deployment-upgrade handling before enabling a projection writer. |
| Production database and deployment | Confirm managed PostgreSQL, existing data to migrate, secrets delivery, monitoring, and hosting. Local Docker Compose is optional tooling, not a production decision. |
| Redeem code handling | The current contract stores/reveals plaintext codes. Confirm whether these are secrets and what off-chain delivery is expected. |
| API exposure | Catalog reads are currently public. Decide account-specific data, tenant access, rate limits, and external API compatibility before adding private features. |

See `contract-validation.md` for the source evidence that constrains these decisions.
