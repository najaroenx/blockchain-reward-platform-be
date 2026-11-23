# Python backend architecture

Status: Python foundation, read-only catalog, coupon-type draft persistence (behind
`COUPON_TYPES_ENABLED`, default off), redeem route and use case, and merchant
login/session/lockout implemented. Redeem admission has no production adapters and
rejects work by default. JWT signing uses an ES256 adapter behind the TokenSigner port
(#182) and the same issuer/audience are verified by redeem. Live chain integration is
not enabled.

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
    domain/entities.py             CouponType/CouponHolding chain snapshots, CouponTypeDraft
                                    pre-chain record (no chain_type_id until a later phase)
    application/dto.py             draft create input, redeem command/payload/result
    application/exceptions.py      draft application failures, redeem error codes and failures
    application/ports.py           draft repository; wallet, deployment, and admission contracts
    application/use_cases.py       idempotent draft create/get/list; resolve deployment and
                                    admit a fixed amount of one for redeem
    infrastructure/models.py       coupon_types and idempotency-key projections (drafts)
    infrastructure/repository.py   idempotent draft create/replay adapter
    infrastructure/mock_auth.py    local fixture token mapped to a fixed user and wallet
    infrastructure/jwt_auth.py     ES256 verification before trusted wallet resolution
    infrastructure/admission.py    atomic SQL admission and byte-identical idempotency replay
    infrastructure/contract_errors.py  classification of decoded errors with established origin
    presentation/router.py         coupon-type routes (mounted only if COUPON_TYPES_ENABLED)
                                    and the redeem route with injected dependencies
    presentation/errors.py         centralized error status, message, headers, and OpenAPI examples
    presentation/schemas.py        draft and redeem HTTP validation/response models
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
tests/integration/coupons/          redeem HTTP, signed JWT, and real SQL admission tests
tests/integration/                 shared migration and process tests
scripts/verify.py                  repeatable verification entry point
scripts/verify_redeem_api.py       OpenAPI and Postman export/check; optional Newman fixtures
scripts/sync_contract_reference.py reproducible ABI extraction from the pinned Git commit
```

The installable package is `marketplace` under `src/`. Organize by feature first, then by domain,
application, infrastructure, and presentation within each feature. Listings owns the existing
catalog read use cases, projection models, repository, and HTTP endpoints. Coupons now owns two
independent slices that happen to share the feature package:

- A Phase 1 slice of BE-0602 (issue #222): `POST`/`GET /api/v1/coupon-types` persist and read a
  `CouponTypeDraft` locally, with `chain_type_id` always `NULL`. The route only exists when
  `COUPON_TYPES_ENABLED=true` (default `false`). Coupon *minting*, and ever submitting
  `createCouponType` on chain, still require the deployment, signing, authentication, and queue
  decisions below — Phase 1 deliberately does not touch any of them, so `chain_type_id` has no
  path to becoming non-null yet. `name` uniqueness is global in Phase 1 because no merchant/tenant
  model exists; revisit once "Authentication" and "API exposure" below are resolved.
- The redeem route, concrete DTOs, admission use case, and SQL admission adapter (BE-0608, below).
  `CouponType`/`CouponHolding` here are indexed on-chain projections, unrelated to and more
  "canonical" than the Phase 1 `CouponTypeDraft` above — the name split is deliberate, not a typo.

Note the two slices also carry two independent idempotency mechanisms: the Phase 1 draft feature
has its own bespoke `coupon_type_idempotency_keys` table, while redeem admission uses the shared,
general `infrastructure/{idempotency,operation_models}.py`. Unifying the draft feature onto the
shared mechanism is a reasonable future cleanup, not done here to keep this merge's blast radius
contained.

Shared connection management and ABI reference assets stay in the package-level infrastructure.
Feature infrastructure owns feature projection models and queries. Shared operation, idempotency,
and outbox models belong to shared infrastructure, which does not import features. Database
migrations explicitly register both shared and implemented feature models.

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
With neither JWT configuration nor mock auth and no injected dependencies, a supplied token returns
`503 REDEEM_UNAVAILABLE`. Missing bearer credentials return 401. Only the redeem route maps
request validation failures to HTTP 400. Catalog validation retains its existing HTTP 422 behavior.

`JWT_PUBLIC_KEY_FILE`, `JWT_ISSUER`, and `JWT_AUDIENCE` configure ES256 verification compatible
with #182. All three are required together, and mock auth must be disabled. The public P-256 key
is loaded at startup. The verifier checks signature, issuer, audience, expiry, required identity
claims, and the RFC 7638 key ID before calling `WalletResolver`. Invalid tokens return 401.
The default JWT configuration has no wallet resolver and returns 503 after token verification;
trusted wallet authorization remains separate work. Real signed-token HTTP
tests prove rejected tokens cause no wallet lookup, admission call, or SQL write on SQLite and
optional PostgreSQL. Their operation/outbox write probes do not implement production admission.

`SqlRedeemAdmissionStore` now provides actual admission separately from the AC2 probes. Shared
operation/idempotency/outbox models live in `infrastructure/operation_models.py` and migration
`0002_redeem_admission`; the feature adapter depends on that shared persistence layer. It claims
idempotency first, commits operation/payload/outbox/acknowledgement together, and returns only after
commit. Fingerprinting is shared in `infrastructure/idempotency.py`. PostgreSQL admission uses
READ COMMITTED for replay after a concurrent insert commits; other readers keep their isolation.
The default app still lacks trusted wallet/deployment adapters and remains unavailable for writes.
Real admission HTTP tests verify 20 overlapping requests create one operation and outbox.

Explicit `worker/redeem.py` and `indexer/redeem.py` use persisted signed transactions and receipt
correlation through `coupons/infrastructure/execution_store.py` and `receipts.py`. Migration
`0003_redeem_execution` adds transaction steps, deduplicated events, mined results and preflight
refusals. The local test signer checks raw calldata/identity before handing bytes to the handler.
The production CLI remains disabled; signing/nonce allocation, outbox publication and finality/reorg
adapters require separate confirmation. See [redeem state](redeem-state.md) for the short status.

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

Migration `0006_auth_login` adds the FEAT-03 identity/session foundation because the Python base
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
401 response and blocks pending descendants from extending the family. Migration `0007_disabled_sessions`
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
No public endpoint submits a transaction, mints coupons, or releases escrow. The coupon-types
endpoint (behind `COUPON_TYPES_ENABLED`, default off) is the one local write: it persists a row
that a later, explicitly-scoped phase would still need a worker, signer, and queue design to ever
turn into an on-chain `createCouponType` call.

`RedeemCoupons` copies the trusted wallet, chain, and contract into a payload with `amount=1`.
The route returns HTTP 202 only after the admission adapter returns its acknowledgement.
The adapter contract requires atomic idempotency, operation, and outbox persistence before returning.
The SQL adapter implements that persistence. Its HTTP tests use actual migrated tables for DB
commit, replay, and concurrency, with wallet/deployment fixtures. No live deployment is inferred.

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
| Authentication | Merchant login/session/lockout and ES256 JWT signing/verification (#177/#182) are implemented and share one issuer/audience. Still needed: provisioning the mounted PEM through deployment secrets, key rotation (#184), customer OTP, account roles, revocation APIs, and wallet-to-user authorization. |
| Points | Confirm the deployed ERC-20 addresses and precision. Contract source accepts ERC-20 payments, not a backend-only points balance or native ETH. |
| Queue and operations | Select queue/storage, retries, idempotency scope, nonce ownership, replacement policy, and receipt/finality tracking before implementing a transaction worker. |
| Indexing and reorgs | Select start block, finality policy, replay/reorg rollback, checkpoint ownership, and deployment-upgrade handling before enabling a projection writer. |
| Production database and deployment | Confirm managed PostgreSQL, existing data to migrate, secrets delivery, monitoring, and hosting. Local Docker Compose is optional tooling, not a production decision. |
| Redeem code handling | The current contract stores/reveals plaintext codes. Confirm whether these are secrets and what off-chain delivery is expected. |
| API exposure | Catalog reads are currently public. Decide account-specific data, tenant access, rate limits, and external API compatibility before adding private features. |

See `contract-validation.md` for the source evidence that constrains these decisions.
