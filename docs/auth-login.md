# Merchant login for issue #177

`POST /v1/auth/login` verifies merchant-admin credentials, records attempts, enforces lockout,
and persists refresh-token sessions. It uses the [ES256 signer and public JWKS](jwt-signing.md)
when all JWT settings are configured. Key rotation remains in #184. Development/test without JWT
configuration returns `503 AUTH_UNAVAILABLE` for valid credentials and creates no session or cookie.
The normal API never returns a mock access token.

## Source validation and scope

The implementation was compared with [issue #177](https://github.com/najaroenx/blockchain-reward-platform/issues/177),
the FEAT-03 [service](https://github.com/najaroenx/blockchain-reward-platform/blob/main/reference/feat-03/src/auth.service.ts),
[types](https://github.com/najaroenx/blockchain-reward-platform/blob/main/reference/feat-03/src/types.ts),
and [OpenAPI](https://github.com/najaroenx/blockchain-reward-platform/blob/main/reference/feat-03/api/openapi.yaml).

- Issue paths describe TypeScript. This backend uses `src/marketplace/auth/` with four feature layers.
- The issue assumes auth tables already exist. The Python base had only catalog projections;
  migration `0002_auth_login` adds the seven FEAT-03 foundation tables. It does not execute legacy SQL.
- The OpenAPI reference sends refresh tokens only through a cookie. The response body contains
  `accessToken`, `tokenType`, `expiresIn`, and `subject`.
- The reference service commits a session before calling its signer. Here signing happens before
  the session transaction, so a signer failure cannot leave an undisclosed session.
- The reference OpenAPI lists fewer JWT claims than its service/types. The `AccessClaims` input follows
  the service/types: `sub`, `kind`, `mid`, `sid`, `jti`, `iat`, and `exp`. No roles or permissions are included.
  The #182 adapter adds configured issuer/audience claims and a public-key-derived `kid`.
- The generic test plan mentions duplicate Idempotency-Key requests, but the login contract does not
  require that header. Independent successful logins create separate sessions.

Login is off chain. It does not change wallet ownership, whitelist status, signing authority,
coupon balances, or contract settlement. Other services must check current account status and
wallet authorization before accepting sensitive work.

## Request and responses

The request accepts only email and password. Email normalization is NFC, trim, then lowercase.
Password length is 1 to 256 characters. Passwords are not trimmed.

```json
{"email":"owner@shop.example","password":"user-supplied-password"}
```

With a configured signer and valid credentials, HTTP 200 returns the access token and subject.
Access-token lifetime is 900 seconds. `merchantStatus` reflects current database state;
`PENDING` merchants can log in. A disabled merchant-admin is rejected.

The refresh token contains 256 random bits and is sent as cookie `refreshToken` with
`HttpOnly; Secure; SameSite=Strict; Path=/v1/auth; Max-Age=2592000`. It is not included in JSON.
The database stores only its SHA-256 hash. Secure cookies require HTTPS when used by a browser.
All login responses include `Cache-Control: no-store` and `Pragma: no-cache`.

| HTTP | Code | Behavior |
| --- | --- | --- |
| 400 | `VALIDATION_FAILED` | Invalid input, extra fields, or malformed JSON; rejected input is not echoed. |
| 401 | `INVALID_CREDENTIALS` | Unknown email, wrong password, and disabled admin use the same message. |
| 423 | `ACCOUNT_LOCKED` | Five bad credentials within 900 seconds cause lockout; includes Retry-After. |
| 429 | `RATE_LIMITED` | Peer-IP request budget exhausted; includes Retry-After. |
| 503 | `AUTH_UNAVAILABLE` | Missing/failed signer or storage failure; infrastructure details are hidden. |

Errors include a generated `requestId`. The generated [OpenAPI](api/login.openapi.json) and
[Postman collection](../postman/login.postman_collection.json) describe the HTTP contract.

## Password work and concurrency

Production password verification uses Argon2id with memory cost 65536 KiB, time cost 3, and
parallelism 4. Missing or malformed hashes still perform dummy verification with the same parameters.
Hashes with another parameter profile are rejected through that dummy path. Provisioning must use
the same approved password profile. Tests verify matching Argon2 work; they do not claim identical
end-to-end network latency.

The peer-IP budget defaults to 20 requests in 60 seconds. `LOGIN_IP_LIMIT` and
`LOGIN_IP_WINDOW_SECONDS` configure it. The API reserves a hashed `PENDING` attempt before password
work or signing. IP-budget rejection does not create another attempt. Lockout checks count only
completed `BAD_CREDENTIALS` attempts. Incomplete attempts never create sessions or increment lockout.

PostgreSQL writes use READ COMMITTED and transaction advisory locks scoped separately by peer IP
and normalized-email hash. SQLite uses BEGIN IMMEDIATE and enables foreign-key checks on every
connection. Password work and signing occur outside those transactions.

The session transaction rechecks the admin's disabled flag, password hash, merchant, and normalized
email after signing. It commits the session, successful attempt, last-login time, and login event
together. A storage failure rolls the whole session transaction back.

`counts_for_lockout` marks which bad attempts count toward the current failure window. Success
clears those flags under the identifier lock without deleting audit history. Reset does not depend
on timestamp ordering, including equal timestamps and overlapping requests.

The server entry point disables forwarded-header interpretation. Rate limiting hashes the actual
peer address rather than accepting caller-supplied X-Forwarded-For. Trusted reverse-proxy handling
requires a separate deployment decision.

## Database foundation

The migration creates `merchant`, `merchant_admin`, `customer`, `auth_session`, `login_attempt`,
`login_lockout`, and `auth_event`. The reduced FEAT-03 customer table supports session-owner foreign
keys; OTP, customer registration, merchant onboarding, and their schema reconciliation remain in
their own tasks. [Refresh rotation](auth-refresh.md) is implemented by #178; logout remains in #179.

| Issue acceptance | Local proof |
| --- | --- |
| T1 | Duplicate refresh hashes violate the unique index. |
| T2 | A second child session for one parent violates the unique parent index. Refresh HTTP races also create only one child. |
| T3 | Family-revocation storage updates leave no unrevoked member; repeated revocation changes zero rows. |
| T4 | Revocation timestamp and reason must be present together. |
| T5 | Exactly one merchant-admin or customer owns a session; foreign keys and subject-kind checks apply. |
| T8 | No table has a plaintext access-token or refresh-token column. |

These checks pass on temporary SQLite databases and on PostgreSQL 17.11 from the local
`postgres:17-alpine` Compose container. The PostgreSQL auth/migration run passed 31 tests,
including all six storage invariants, concurrent login/lockout, rollback, schema drift,
upgrade/downgrade, and re-upgrade. This verifies T2/T3 at the database/repository layer;
Refresh API flow is verified by #178; logout remains in #179. No shared staging deployment is claimed.

## Repeatable checks

```bash
.venv/bin/python scripts/verify.py
.venv/bin/python scripts/verify_login_api.py
```

The API check validates OpenAPI and detects drift in the generated OpenAPI/Postman files.
Regenerate them after a contract change:

```bash
.venv/bin/python scripts/verify_login_api.py --export
```

With Newman available, run the full collection against an isolated seeded SQLite app:

```bash
.venv/bin/python scripts/verify_login_api.py --newman newman
```

For PostgreSQL, start the local service and supply an admin connection used only to create
randomly named disposable databases. Each test database is removed after use; the supplied
admin database is not migrated, seeded, or dropped.

```bash
docker compose pull postgres
docker compose up -d --wait postgres
export TEST_POSTGRES_URL='postgresql+psycopg://reward:local-development-only@127.0.0.1:5432/reward'
.venv/bin/pytest tests/integration/auth tests/integration/test_migrations.py -q
.venv/bin/python scripts/verify_login_api.py --newman newman --postgres
```

The script uses real Argon2 verification and database writes. It signs with an ephemeral ES256 key
and verifies tokens against HTTP JWKS. The original #177
run passed 25 requests and 103 assertions; the #182 collection also exercises public JWKS and real JWTs.
Run the collection's `Local validation` folder against a migrated local API;
the `Fixture login` folder belongs to the isolated verifier and its seeded accounts.
