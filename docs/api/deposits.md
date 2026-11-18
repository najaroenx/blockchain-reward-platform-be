# Deposit admission

`POST /v1/deposits` creates a pending THB deposit for the merchant in a verified
merchant-admin access token. The body contains only `amountSatang`, a canonical
decimal string from `"2000"` to `"5000000"`. These inclusive per-deposit limits
are provisional. The server multiplies the amount by 10^16 to calculate the
18-decimal base-unit amount. The database enforces the exact conversion.

Send an `Idempotency-Key` of 8 to 255 characters. The first successful request
returns HTTP 201 with `depositId`, `amountSatang`, `amountBase`,
`status: PENDING_PAYMENT`, `paymentUrl`, and `expiresAt`. An identical request
from the same merchant returns HTTP 200 with the original response bytes, as
specified by the FEAT-12 OpenAPI. Reusing the key for another amount returns 409.
The payment adapter receives a stable reference outside the write transaction.
It must reuse its checkout for the same reference and reject a different amount.
The database commits the deposit and response together.

`GET /v1/deposits/{depositId}` returns status to an authenticated administrator
of the owning merchant. Missing and foreign deposits both return 404. A pending
deposit is reported as `EXPIRED` once the checkout expires. Admission creates
no mint operation. Payment confirmation and mint projection belong to
[#237](https://github.com/najaroenx/blockchain-reward-platform/issues/237) and
[#247](https://github.com/najaroenx/blockchain-reward-platform/issues/247).

## Run locally

Apply migrations with `.venv/bin/alembic upgrade head`. Set
`MOCK_DEPOSIT_PSP_ENABLED=true` for the local API. Use the existing mock token
settings or configure the ES256 JWT verifier. The mock issues a
`https://payments.invalid` checkout URL. Without a configured PSP adapter, a new
deposit returns `503 PAYMENT_UNAVAILABLE`. The mock flag is blocked in
production.

Import the [Postman collection](../../postman/deposits.postman_collection.json)
or run its six requests against a temporary migrated database.

```bash
.venv/bin/python scripts/verify_deposit_api.py --newman newman
```

[OpenAPI for deposit admission](deposits.openapi.json)
