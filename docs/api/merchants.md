# Merchant signup

`POST /v1/merchants` accepts the merchant name and the first administrator's
email, password, and display name. Sign-up is anonymous and requires an
`Idempotency-Key` of 8 to 255 characters. The first request returns 201 with the
merchant, wallet address, and provision operation ID. The merchant and provision
operation start as `PENDING`. The wallet starts with `ready: false`.

Retries for the same normalized email and key return the saved 201 response
and `Location`. The fingerprint covers normalized non-secret fields. The
server checks the retry password against the administrator's salted Argon2id
hash. A change to the signup fields or password returns 409. A password change
can invalidate a later replay of the signup. The server allocates the wallet
reference and hashes the password outside the write transaction.

The database commits the merchant, admin, wallet reference, operation, two
planned steps, initial history event, versioned outbox entry, and response
together. Wallet storage contains the address and opaque reference. The
published plan starts with owner-signed Marketplace `addToWhitelist`, followed
by wallet-signed Coupon `setApprovalForAll`. Signing, broadcasting, and
activation belong to [#175](https://github.com/najaroenx/blockchain-reward-platform/issues/175).
The production wallet provider and target deployment are still unverified.
The parallel FEAT-03 auth migrations must be reconciled with the merchant and
admin tables when those branches are integrated.

## Verify locally

Apply migrations and set `MOCK_MERCHANT_WALLET_ENABLED=true` for the local API.
The mock returns simulated addresses. A fresh signup without a wallet provider
returns 503. The default registration limit is 30 valid requests per minute
per client IP and process. The process-local limit is configurable through
`MERCHANT_SIGNUP_PER_MINUTE`.

```bash
.venv/bin/python scripts/verify_merchants_api.py --newman newman
```

[OpenAPI specification](merchants.openapi.json) · [Postman collection](../../postman/merchants.postman_collection.json)
