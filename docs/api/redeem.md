# Redeem admission API

`POST /v1/coupons/redeem` admits one coupon for the authenticated wallet.
The route, DTOs, error mapping, OpenAPI, and Postman artifacts are implemented.
ES256 access-token verification is available through a configured public key.
`SqlRedeemAdmissionStore` implements durable admission when injected with trusted wallet/deployment
adapters. The default app's wallet mapping, deployment resolution, and admission remain unconfigured.
The local mock therefore returns 503 for a valid request. Issue #224 is not complete.

The generated [OpenAPI document](redeem.openapi.json) describes operation `redeemCoupon`.
The [Postman collection](../../postman/redeem.postman_collection.json) and
[local environment](../../postman/local.postman_environment.json) use that same response contract.

## Request and acknowledgement

The endpoint requires a bearer access token, JSON content, and `Idempotency-Key` of 8 to 255 characters.
The body contains only `typeId`, a canonical decimal string from 1 through `2^256 - 1`.
Wallet, chain, contract, and amount cannot be supplied in the body.
The server resolves the context and fixes amount at 1.

```json
{"typeId":"1"}
```

The success schema is HTTP 202 with these fields:

```json
{"operationId":"op_example","status":"PENDING","typeId":"1","amount":"1"}
```

The admission adapter must return only after an atomic commit or replay of a committed acknowledgement.
`PENDING` does not confirm redemption, reveal a code, or promise settlement.
The current 202 examples run against test adapters and do not prove a database commit.

## Access-token verification (AC2)

Configure the public counterpart of the #182 signing key and the same issuer and audience:

```dotenv
MOCK_AUTH_ENABLED=false
JWT_PUBLIC_KEY_FILE=/run/config/jwt-signing-public.pem
JWT_ISSUER=https://auth.example.com
JWT_AUDIENCE=reward-api
```

The three JWT settings must be supplied together. Startup rejects unreadable keys, private keys,
and public keys other than P-256. Configuring JWT verification and mock auth together is an error.
The public key is loaded once at startup; replacing it requires restarting the API.
Only the configured key is accepted, with the RFC 7638 `kid` used by #182. Key overlap during
rotation requires a future key-set adapter.

`JwtWalletAuthenticator` fixes the allowed algorithm to ES256 and verifies signature, issuer,
audience, expiry, issued-at, and any not-before claim, with zero clock leeway. It requires the
current #182 claims `sub`, `mid`, `sid`, `jti`, `kind`, `iat`, `exp`, `iss`, and `aud`, a JWT type
header, and the configured key's `kid`. The supported subject kind is `MERCHANT_ADMIN`.
Expired, malformed, unsigned, or rejected tokens receive a sanitized 401 with a Bearer challenge
before wallet lookup, deployment lookup, or admission. Access tokens are not persisted.

Verification alone does not authorize a wallet. The default app returns 503 for a verified token
until a trusted wallet resolver and the remaining admission adapters are configured. An injected
resolver may return no authorized wallet, which produces 403. Wallet mapping remains AC3.
With neither JWT configuration nor mock auth, a supplied bearer token still receives 503.

`tests/integration/coupons/test_jwt_auth.py` signs real ES256 tokens and checks rejected requests
produce zero wallet/deployment lookups, zero admission calls, zero SQL writes, and unchanged SQL
write-probe tables. A valid-token control reaches admission and writes both probes.
Those AC2 tables are test instrumentation, not the production operation/outbox schema.
SQLite is the default. To repeat these checks on a disposable PostgreSQL database:

```bash
REDEEM_AUTH_TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  .venv/bin/python -m pytest tests/integration/coupons/test_jwt_auth.py -q
```

The PostgreSQL tests create and remove a separate schema per request test.
They prove the AC2 rejection boundary. The separate AC4–7 tests below use the actual admission tables.
See the [PyJWT decoding API](https://pyjwt.readthedocs.io/en/stable/api.html#jwt.decode)
for algorithm restrictions and required-claim validation.

## Durable admission and idempotency (AC4–7)

Migration `0002_redeem_admission` creates shared `operation`, `idempotency_key`, and `outbox`
tables. Apply migrations before configuring `SqlRedeemAdmissionStore(database.engine)` as the
`RedeemAdmissionStore` supplied to `RedeemCoupons`. The composition root still needs an
authentication callback that proves wallet ownership and a verified `CouponDeploymentResolver`.
The default app and mock-token setup continue returning 503 for valid requests.

Admission performs this sequence in one transaction:

1. Insert `idempotency_key ON CONFLICT (scope, key) DO NOTHING` as the first statement.
2. If the key exists, compare its fingerprint and replay the stored acknowledgement, or reject it.
3. For a new key, insert the PENDING `COUPON_REDEEM` operation and an outbox record referencing it.
4. Store the HTTP 202 acknowledgement on the idempotency record, then commit before returning.

The operation payload is the authoritative worker input; outbox references it by operation ID.
It stores `chainId`, `couponContractAddress`, `walletAddress`, `typeId`, and `amount`, all as strings.
Addresses are lowercase, uint256 values retain every digit, and amount is always `"1"`.
There is no token, redeem code, predicted settlement, per-code reservation, or per-type unique lock.
Outbox publication remains #244 work. Explicit handler/receipt projection is tested on a pinned
local chain; production confirmation/reorg/signing adapters remain open. See [redeem state](../redeem-state.md).

Idempotency scope is `user:<verified-user-id>:POST:/v1/coupons/redeem`. The fingerprint is unsalted
SHA-256 of compact, key-sorted, NFC-normalized JSON containing the resolved payload. It includes
wallet, chain, contract, type, and amount. A golden vector matches the #147 JavaScript formula.
Changing any resolved value with the same key returns 409 `IDEMPOTENCY_KEY_REUSED`.
Different users or different keys have independent admissions, including the same wallet/type.

An acknowledgement committed by admission is replayable even while its chain operation is PENDING.
Concurrent duplicate inserts wait for the winner's commit, then replay the same 202 bytes, as #147
requires. A committed claim without an acknowledgement returns 409 `IDEMPOTENCY_KEY_IN_PROGRESS`.
Later operation status changes do not change the original admission acknowledgement.
Response bodies use SQL text to preserve exact bytes instead of the historical pack's JSONB response
column. The HTTP boundary validates a stored response against its schema before returning its bytes.
This is a new Python migration; it does not modify or apply the legacy reference SQL.

The admission adapter uses PostgreSQL READ COMMITTED so a duplicate's SELECT can see the
acknowledgement committed while its INSERT was waiting. Other readers retain their configured
isolation. See [PostgreSQL isolation semantics](https://www.postgresql.org/docs/current/transaction-iso.html#XACT-READ-COMMITTED).
SQL failures roll back the entire transaction before becoming a sanitized 503
`ADMISSION_UNAVAILABLE`; they are not interpreted as duplicate keys. Expiry is recorded at 72 hours,
but no cleanup or expired-key reuse is implemented, preserving existing operation correlation.

`tests/integration/coupons/test_admission.py` uses the actual migrated tables and SQL adapter through
HTTP. Only authentication/wallet and deployment resolution are fixtures. It covers committed payloads,
byte-identical replay after a new engine/app, changed-context conflicts, independent keys/users,
20 overlapping requests, commit-before-202, and rollback/retry after injected SQL failures.
The concurrency test holds the winner's uncommitted insert until a competing insert arrives.

```bash
REDEEM_ADMISSION_TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  .venv/bin/python -m pytest tests/integration/coupons/test_admission.py -q
```

Use a disposable PostgreSQL database; each test migrates and removes its own schema. The default
suite uses file-backed SQLite with foreign keys enabled. These checks establish database admission
behavior, not wallet ownership, a deployed contract, signer rights, outbox publication, or redemption.

## Public errors

Errors use one envelope with stable public codes and messages:

```json
{"error":{"code":"REDEEM_UNAVAILABLE","message":"Redeem admission adapters are not configured."}}
```

`src/marketplace/coupons/application/exceptions.py` defines the codes and `RedeemFailure` adapter exception.
`src/marketplace/coupons/presentation/errors.py` defines HTTP status, public message, headers, and OpenAPI examples.
Adapters report expected failures with `RedeemFailure`; they do not expose raw SQL, RPC, or framework exceptions.
The HTTP boundary sanitizes unexpected exceptions to `INTERNAL_ERROR`.

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `IDEMPOTENCY_KEY_REQUIRED` | Required header is missing. |
| 400 | `VALIDATION_FAILED` | Invalid body, malformed JSON, invalid key length, or extra fields. |
| 401 | `UNAUTHENTICATED` | Missing, invalid, or rejected token. Includes `WWW-Authenticate: Bearer`. |
| 403 | `WALLET_FORBIDDEN` | No wallet authorized for this user. |
| 404 | `COUPON_TYPE_NOT_FOUND` | Type not found in the resolved deployment. |
| 409 | `IDEMPOTENCY_KEY_IN_PROGRESS`, `IDEMPOTENCY_KEY_REUSED` | Existing request is in progress, or key conflicts with its payload. |
| 422 | `INSUFFICIENT_BALANCE`, `COUPON_NOT_STARTED`, `COUPON_EXPIRED`, `NO_REDEEM_CODE_AVAILABLE` | Known Coupon business rejection before admission. Request validation uses 400. |
| 429 | `RATE_LIMITED` | Limiter rejection. `Retry-After` is optional and contains seconds supplied by the limiter. |
| 503 | `REDEEM_UNAVAILABLE` | Required admission adapters are not configured. |
| 503 | `SIGNER_UNAUTHORIZED` | Backend signer is unauthorized on chain. |
| 503 | `CONTRACT_CALL_FAILED` | Callback, unknown-origin, or unrecognized contract failure. |
| 503 | `ADMISSION_UNAVAILABLE` | Admission storage is unavailable. |
| 500 | `INTERNAL_ERROR` | Unexpected exception or invalid adapter result. Public details are sanitized. |

These mappings describe the adapter contract. JWT verification is exercised separately above;
wallet authorization, rate-limiter policy, and deployed contract checks remain open.
Real SQL idempotency is tested separately from the Postman fixture responses.

## Contract error origin

`classify_redeem_revert` classifies an already-decoded error with origin `coupon`, `callback`, or `unknown`.
The caller must establish origin independently. Coupon and Vault share `InsufficientBalance()` selectors.
The transaction destination and error name cannot determine whether Coupon or a callback reverted.
Callback and unknown-origin failures map to `CONTRACT_CALL_FAILED` 503.

For a known Coupon-origin error, the helper maps its recognized business errors to 404 or 422.
Known Coupon `Error("Not authorized to redeem")` maps to `SIGNER_UNAUTHORIZED` 503 for backend signing.
The helper does not decode RPC payloads, establish error origin, or verify a deployment.

After a 202 acknowledgement, #244 must record a later chain failure in the operation result.
The API handler does not write those results or change the original HTTP response. The explicit
redeem handler/projector records local-chain outcomes separately from the admission acknowledgement.

## Artifact and fixture checks

The default check validates OpenAPI, detects generated-artifact drift, and validates Postman examples
against response schemas. It does not send HTTP requests. `scripts/verify.py` includes this check.

```bash
.venv/bin/python scripts/verify_redeem_api.py
```

Regenerate all three artifacts after changing the API contract:

```bash
.venv/bin/python scripts/verify_redeem_api.py --export
```

The Postman folders run in different modes:

| Folder | Target | Expected behavior |
| --- | --- | --- |
| `Local mock` | Current API with mock auth enabled and the configured token. | Missing or wrong token gets 401, invalid input gets 400, valid request gets 503. |
| `Adapter contract` | Isolated app created by `verify_redeem_api.py` with fixture adapters. | Exercises 202, typed errors, and sanitized unexpected failures. IDs represent test scenarios. |

The environment defaults to `http://127.0.0.1:3000` and token `mock-redeem-token`.
The [README mock-token instructions](../../README.md#ทดลองด้วย-mock-token) configure the local API.
Run only the `Local mock` folder against that API. Fixture coupon IDs and tokens do not represent deployed data.

When Newman is available in PATH, run both folders through the script:

```bash
.venv/bin/python scripts/verify_redeem_api.py --newman newman
```

The script starts separate loopback servers and supplies each folder's fixture URL and token.
These Postman requests verify HTTP behavior and response mapping. They do not prove token expiry,
wallet ownership, DB commits, idempotency, rate-limit policy, or chain behavior.
The expired-token fixture is a fixed rejected token, not a token-expiry implementation.
