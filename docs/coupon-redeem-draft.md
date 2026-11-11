# Redeem draft — issue #224

This draft defines HTTP schemas, application DTOs, and adapter protocols. It does not
register a route or implement authentication, admission, database writes, or chain calls.

## Data flow to implement

1. Auth verifies the access token and identifies the application user. JWT claim names
   and issuer configuration remain owned by the auth task.
2. Auth supplies an `AuthenticatedWallet`, or `WalletResolver` resolves one for that user.
   Constructing this DTO alone does not verify identity or wallet ownership.
3. Presentation validates `RedeemCouponRequest` and constructs `RedeemCouponCommand`.
   The body contains only a canonical decimal `typeId` in the range 1 through 2^256 - 1.
4. `CouponDeploymentResolver` selects a verified deployment from trusted configuration.
5. The future use case creates `RedeemCouponPayload`, with amount fixed at 1.
6. `RedeemAdmissionStore` atomically records idempotency, operation, and outbox. The
   result becomes a `RedeemCouponResponse` only after commit, or when replaying a
   previously committed acknowledgement.

`PENDING` acknowledges admission, not successful redemption. The worker uses the
original operation ID and calls `redeemFrom(wallet_address, type_id, 1)` under #244.

## Dependencies still open

- Agree the auth adapter and wallet mapping with the token task.
- Confirm chain, addresses, implementation revision, callback wiring, and signer rights.
  The bundled ABI pins `1dc42d5`; the issue cites `5639bfe`. Deployment remains unverified.
- Agree schema and idempotency outcomes with #147/#213, including fingerprinting the
  resolved wallet and deployment, replay, key conflicts, and concurrent requests.
- Update #244 from the legacy per-code burn flow to `redeemFrom` and receipt/events.
- Map request validation failures to HTTP 400 when adding the route. Pydantic schemas
  alone do not implement HTTP error mapping, token checks, or header validation.

Contract evidence and settlement behavior are recorded in [contract validation](contract-validation.md).
Burning, consuming a FIFO code, and applicable settlement happen within one transaction.
