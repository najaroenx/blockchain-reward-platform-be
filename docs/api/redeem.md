# Redeem admission API

`POST /v1/coupons/redeem` admits one coupon for the authenticated wallet.
The route, DTOs, error mapping, OpenAPI, and Postman artifacts are implemented.
Production auth, deployment resolution, and durable admission remain unconfigured.
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

These mappings describe the adapter contract. They do not establish production auth, idempotency,
rate-limiter policy, or deployed contract checks.

## Contract error origin

`classify_redeem_revert` classifies an already-decoded error with origin `coupon`, `callback`, or `unknown`.
The caller must establish origin independently. Coupon and Vault share `InsufficientBalance()` selectors.
The transaction destination and error name cannot determine whether Coupon or a callback reverted.
Callback and unknown-origin failures map to `CONTRACT_CALL_FAILED` 503.

For a known Coupon-origin error, the helper maps its recognized business errors to 404 or 422.
Known Coupon `Error("Not authorized to redeem")` maps to `SIGNER_UNAUTHORIZED` 503 for backend signing.
The helper does not decode RPC payloads, establish error origin, or verify a deployment.

After a 202 acknowledgement, #244 must record a later chain failure in the operation result.
This API change does not write those operation results or change the original HTTP response.

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
These requests verify HTTP behavior and response mapping. They do not prove token expiry,
wallet ownership, DB commits, idempotency, rate-limit policy, or chain behavior.
The expired-token fixture is a fixed rejected token, not a token-expiry implementation.
