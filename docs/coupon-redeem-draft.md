# Redeem admission for issue #224

`POST /v1/coupons/redeem` is registered in `main.py`. The route validates requests,
builds a concrete command, and calls `RedeemCoupons` to construct a payload with `amount=1`.
ES256 token verification is implemented for AC2. SQL admission and idempotency are implemented
for AC4–7 when injected with trusted context. Wallet mapping and deployment resolution remain open.

`create_app(..., redeem=RedeemDependencies(...))` supplies the authentication callback and
an optional use case with its adapters. Authentication can be wired before deployment and admission.
With mock auth disabled and no injected dependencies, a request with a bearer token returns
`503 REDEEM_UNAVAILABLE`. Missing bearer credentials return 401.

## Local mock token

`MOCK_AUTH_ENABLED=true` in `.env.api` enables `MockTokenAuthenticator` when no dependencies
are injected. The flag defaults to `false`, and settings validation rejects enabling it in production.
The default mock profile uses token `mock-redeem-token`, user `mock-user`, and wallet
`0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`. `MOCK_AUTH_TOKEN`, `MOCK_AUTH_USER_ID`, and
`MOCK_AUTH_WALLET_ADDRESS` override those values.

The authenticator compares the exact token and returns the fixed profile. It does not verify
JWT signatures, expiry, or wallet ownership. Wrong or missing tokens return 401.
A matching token passes authentication. Invalid request bodies or headers then return 400.
A valid request receives `503 REDEEM_UNAVAILABLE` with
`Redeem admission adapters are not configured.` until a redeemer is supplied.
Mock auth creates no fake deployment, operation, outbox, or HTTP 202 acknowledgement.
The [README mock token example](../README.md#ทดลองด้วย-mock-token) shows configuration and curl.

## Data flow and adapter obligations

1. `JwtWalletAuthenticator` verifies ES256 tokens using the public key, issuer, audience,
   header, and claims emitted by #182. See [AC2 configuration and tests](api/redeem.md#access-token-verification-ac2).
   Verification happens before wallet lookup or admission. Wallet ownership remains AC3.
2. The callback returns an `AuthenticatedWallet`. It can use `WalletResolver` to resolve one.
   Constructing this DTO alone does not verify identity or wallet ownership.
3. Presentation validates `RedeemCouponRequest` and constructs `RedeemCouponCommand`.
   The body contains only a canonical decimal `typeId` in the range 1 through 2^256 - 1.
   `Idempotency-Key` is required and must contain 8 to 255 characters.
4. The use case asks `CouponDeploymentResolver` for a verified deployment from trusted configuration.
5. The use case creates `RedeemCouponPayload` containing chain, Coupon contract, wallet,
   and type ID. The dataclass fixes amount at 1 and does not accept an amount constructor argument.
   The payload contains no access token.
6. The use case calls `RedeemAdmissionStore.admit`. The adapter must atomically record
   idempotency, operation, and outbox before returning, or replay a committed acknowledgement.
   `SqlRedeemAdmissionStore` implements this port using migration `0002_redeem_admission`.
   The route returns HTTP 202 after commit, or replays the stored response bytes.

`PENDING` acknowledges admission, not successful redemption. The worker under #244 must use
the original operation ID and call `redeemFrom(wallet_address, type_id, 1)`.
No worker, projector, or chain calls are added by this route.

## Public error contract

Adapters raise `RedeemFailure` with a `RedeemErrorCode` for expected failures.
The HTTP boundary chooses status, message, and headers from `presentation/errors.py`.
Request validation uses 400, and known Coupon business rejection before admission uses 422.
Authentication uses 401 with a Bearer challenge. Wallet authorization uses 403.
Backend signer, configuration, unknown contract failures, and unavailable admission use 503.
Unexpected exceptions become a sanitized `INTERNAL_ERROR` 500.
The [API reference](api/redeem.md) lists every public code, including idempotency and rate-limit errors.

Contract classification requires an origin established independently of the transaction destination
or error name. Coupon and Vault share `InsufficientBalance()` selectors.
Unknown-origin or callback errors use `CONTRACT_CALL_FAILED` 503.
Known Coupon `Error("Not authorized to redeem")` uses `SIGNER_UNAUTHORIZED` 503 for backend signing.
The classifier does not decode live RPC data or verify a deployment.

If a failure happens after HTTP 202, #244 must record the operation result.
This change does not write post-admission operation failures or change the original HTTP response.

## HTTP verification scope

Request and header validation failures return HTTP 400 only for this route.
A missing key uses `IDEMPOTENCY_KEY_REQUIRED`; other validation failures use `VALIDATION_FAILED`.
Validation responses omit rejected input values. The response schema exposes operation ID, PENDING status,
type ID, and amount only.

`tests/integration/coupons/test_redeem.py` exercises HTTP with adapter doubles. The tests cover
the maximum uint256 value, fixed amount, trusted payload fields, invalid bodies, extra fields,
required headers, adapter rejection, admission failure, default 503 behavior, and OpenAPI.
Those adapter-double tests do not verify token signatures, wallet ownership, durable admission,
or chain behavior. `test_admission.py` separately uses real operation/idempotency/outbox tables
and the SQL adapter to verify AC4–7, including 20 overlapping HTTP requests and atomic rollback.
`test_jwt_auth.py` separately verifies real ES256 signatures and rejection
before wallet lookup or SQL writes, with a valid-token SQL write control. Its database tables
are test probes, not a production admission implementation.

`scripts/verify_redeem_api.py` validates the generated OpenAPI and Postman artifacts.
`Local mock` exercises the current mock-token API, where a valid request still returns 503.
`Adapter contract` uses isolated test adapters for 202 and public error cases.
Its expired-token, wallet, idempotency, and rate-limit fixtures test response mapping, not those policies.
Commands and artifact links are in the [API reference](api/redeem.md).

The first acceptance criterion's request-to-payload path is implemented and tested with those
adapters. This does not complete issue #224 or establish readiness to accept real work.

```bash
.venv/bin/python -m pytest tests/unit/coupons/test_schemas.py tests/integration/coupons/test_redeem.py -q
```

## Dependencies still open

- Connect a trusted subject-to-wallet mapping to the verified #182 token subject (AC3).
- Confirm chain, addresses, implementation revision, callback wiring, and signer rights.
  The bundled ABI pins `1dc42d5`; the issue cites `5639bfe`. Deployment remains unverified.
- Coordinate the new shared admission schema with #147/#213. SQL admission uses #147's
  first-statement claim, unsalted fingerprint, and replay-after-concurrent-commit behavior.
  The response column is text for exact-byte replay; the legacy pack used JSONB.
- #244 has an explicit redeemFrom handler/projector tested locally; production runner,
  signing/nonce ownership, outbox publication and confirmation/reorg policies remain open.
- Wire the implemented SQL admission adapter after wallet/deployment adapters are verified.
- Wire production adapters to the typed error contract, establish RPC error origin, and implement rate limits.
- Confirm the real deployment and production adapters; local post-202 outcomes are tested.
  Current acceptance status is kept in [redeem state](redeem-state.md).

Contract evidence and settlement behavior are recorded in [contract validation](contract-validation.md).
Burning, consuming a FIFO code, and applicable settlement happen within one transaction.
