# Python backend architecture

Status: Python foundation, read-only catalog, and redeem route and use case implemented.
Redeem admission has no production adapters and rejects work by default.
Live chain integration is not enabled.

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
  worker/
    main.py                        shared worker entry point; execution disabled
  indexer/
    main.py                        shared indexer entry point; execution disabled
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
    domain/entities.py             coupon type and holder balance snapshots
    application/dto.py             authenticated context, command, payload, and result
    application/exceptions.py      public error codes and typed adapter failures
    application/ports.py           wallet, deployment, and admission adapter contracts
    application/use_cases.py       resolve deployment and admit a fixed amount of one
    infrastructure/mock_auth.py    local fixture token mapped to a fixed user and wallet
    infrastructure/contract_errors.py  classification of decoded errors with established origin
    presentation/router.py         redeem route and injected dependencies
    presentation/errors.py         centralized error status, message, headers, and OpenAPI examples
    presentation/schemas.py        strict request, acknowledgement, and error models
  infrastructure/
    database.py                    shared engine and request-scoped sessions
    blockchain/abi/                pinned reference, not a deployment binding
migrations/                        Alembic schema owned by this backend
tests/conftest.py                  shared fixtures
tests/unit/                        configuration, logging, and reference tests
tests/integration/listings/         catalog HTTP and database tests
tests/integration/coupons/          redeem HTTP tests using adapter doubles
tests/integration/                 shared migration and process tests
scripts/verify.py                  repeatable verification entry point
scripts/verify_redeem_api.py       OpenAPI and Postman export/check; optional Newman fixtures
scripts/sync_contract_reference.py reproducible ABI extraction from the pinned Git commit
```

The installable package is `marketplace` under `src/`. Organize by feature first, then by domain,
application, infrastructure, and presentation within each feature. Listings owns the existing
catalog read use cases, projection models, repository, and HTTP endpoints. Coupons owns the redeem
route, concrete DTOs, and admission use case. Coupon persistence adapters, creation, and minting
remain unimplemented.

Shared connection management and ABI reference assets stay in the package-level infrastructure.
Feature infrastructure owns its ORM models and queries. Shared infrastructure does not import
features. Database migrations explicitly register the implemented feature models.

The system remains a modular monolith. CLI entry points are `reward-api`, `reward-worker`, and
`reward-indexer`, targeting `marketplace.main`, `marketplace.worker.main`, and
`marketplace.indexer.main`.
Configuration, scripts, migrations, and tests stay at the repository root.

Within listings, domain objects describe listing values. Query DTOs and availability errors belong
to application. Use cases depend on domain and repository protocols. Presentation handles HTTP.
`main.py` wires the concrete repository into the use case and injects its provider into the router.
It also registers `POST /v1/coupons/redeem` and accepts optional `RedeemDependencies` through
`create_app`. Those dependencies supply an authentication callback and an optional `RedeemCoupons`
use case with deployment and admission adapters. Authentication can be wired before admission.
Constructing DTOs does not verify wallets or deployments.

The redeem route requires a bearer token and an `Idempotency-Key` of 8 to 255 characters.
With mock auth disabled and no injected dependencies, a request with a bearer token returns
`503 REDEEM_UNAVAILABLE`. Missing bearer credentials return 401. Only the redeem route maps
request validation failures to HTTP 400. Catalog validation retains its existing HTTP 422 behavior.

`ApiSettings` supports `MOCK_AUTH_ENABLED`, `MOCK_AUTH_TOKEN`, `MOCK_AUTH_USER_ID`, and
`MOCK_AUTH_WALLET_ADDRESS`. Defaults are `false`, `mock-redeem-token`, `mock-user`, and
`0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`. The token is a `SecretStr`.
The settings validator rejects enabled mock auth in production, including directly constructed settings.

When the flag is enabled and no dependencies are injected, `main.py` wires `MockTokenAuthenticator`.
It compares the exact token and returns the configured local user and wallet.
It does not verify JWT signatures, expiry, or wallet ownership. Wrong tokens return 401.
After mock authentication, invalid bodies or headers return 400 before checking for a redeemer.
For a valid request, the absent redeemer returns 503 with
`Redeem admission adapters are not configured.` No operation or outbox is written.
Injected dependencies take precedence over the mock configuration.

Adapters report expected failures with framework-independent `RedeemFailure` and `RedeemErrorCode`.
`presentation/errors.py` owns their HTTP status, public message, response headers, and OpenAPI examples.
Request validation uses 400, while known Coupon business rejections before admission use 422.
Signer/configuration failures use 503. Unexpected exceptions become a sanitized `INTERNAL_ERROR` 500.
Raw SQL, RPC payloads, access tokens, and arbitrary framework exception details are not public errors.

`classify_redeem_revert` accepts an independently established origin of `coupon`, `callback`, or `unknown`.
Coupon and Vault share `InsufficientBalance()` selectors, so transaction destination and error name
cannot establish the origin. Callback, unknown-origin, and unrecognized failures map to
`CONTRACT_CALL_FAILED` 503. The helper does not decode live RPC failures or verify deployments.

`scripts/verify_redeem_api.py` generates the redeem-only OpenAPI document, Postman collection,
and local environment. Its default check validates OpenAPI, rejects artifact drift, and validates
Postman response examples. `scripts/verify.py` includes that check.
Optional Newman execution uses separate loopback servers for `Local mock` and `Adapter contract`.
The latter injects test adapters. Its 202 and error responses do not prove durable admission,
authentication policy, idempotency, rate limiting, or chain results.
The [redeem API reference](api/redeem.md) lists public errors and artifact commands.

Import Linter covers both listings and coupons with six dependency rules: domain independence, application independence,
presentation isolation from persistence, feature infrastructure isolation from HTTP, settings/logging
independence from features, and shared infrastructure independence from features. Domain and
application do not depend on FastAPI, Pydantic, or SQLAlchemy.

## Responsibilities

Contracts own trading authorization on chain, balances, escrow, coupon burns, and settlement.
The backend will own user authentication, proving wallet ownership, request idempotency, transaction
tracking, and projections after those policies are selected. Backend login alone does not authorize
acting as a wallet. Changing the RPC transaction's signer changes `msg.sender` in the contract.

The implemented catalog reads projections only. Each query is scoped by chain ID and marketplace
address. Large EVM integers are stored as exact decimal strings and returned as strings. A missing
checkpoint yields HTTP 503, rather than pretending an unindexed marketplace has no listings.
The returned `indexed_block` describes projection progress; it does not claim the chain is current.
No public endpoint writes listings, submits transactions, mints coupons, or releases escrow.

`RedeemCoupons` copies the trusted wallet, chain, and contract into a payload with `amount=1`.
The route returns HTTP 202 only after the admission adapter returns its acknowledgement.
The adapter contract requires atomic idempotency, operation, and outbox persistence before returning.
That persistence is not implemented. HTTP tests use adapter doubles and do not prove DB commits,
idempotency replay, concurrency, or a live deployment.

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
| Authentication | Choose identity provider or OTP delivery, sessions, account roles, merchant membership, revocation, and wallet-to-user authorization. |
| Points | Confirm the deployed ERC-20 addresses and precision. Contract source accepts ERC-20 payments, not a backend-only points balance or native ETH. |
| Queue and operations | Select queue/storage, retries, idempotency scope, nonce ownership, replacement policy, and receipt/finality tracking before implementing a transaction worker. |
| Indexing and reorgs | Select start block, finality policy, replay/reorg rollback, checkpoint ownership, and deployment-upgrade handling before enabling a projection writer. |
| Production database and deployment | Confirm managed PostgreSQL, existing data to migrate, secrets delivery, monitoring, and hosting. Local Docker Compose is optional tooling, not a production decision. |
| Redeem code handling | The current contract stores/reveals plaintext codes. Confirm whether these are secrets and what off-chain delivery is expected. |
| API exposure | Catalog reads are currently public. Decide account-specific data, tenant access, rate limits, and external API compatibility before adding private features. |

See `contract-validation.md` for the source evidence that constrains these decisions.
