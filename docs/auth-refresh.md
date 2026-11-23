# Refresh-token rotation for issue #178

`POST /v1/auth/refresh` reads the `refreshToken` cookie, returns a signed access JWT, and replaces
the cookie with a new 256-bit random refresh token. The database stores only SHA-256 hashes.
No bearer access token, JSON token, or Idempotency-Key is required.

## Source reconciliation

The implementation follows [issue #178](https://github.com/najaroenx/blockchain-reward-platform/issues/178),
the FEAT-03 [service](https://github.com/najaroenx/blockchain-reward-platform/blob/main/reference/feat-03/src/auth.service.ts),
[OpenAPI](https://github.com/najaroenx/blockchain-reward-platform/blob/main/reference/feat-03/api/openapi.yaml),
and [acceptance SQL](https://github.com/najaroenx/blockchain-reward-platform/blob/main/reference/feat-03/tests/acceptance.sql).

- The issue names TypeScript files. This backend uses the existing Python auth feature layers.
- The reference refresh service omits a merchant's `mid` claim and does not check current account
  disability. Here merchant JWTs retain `mid`, and both merchant-admin and customer status are checked.
  Customer sessions produce customer claims without `mid`; this does not add customer OTP endpoints.
- Reference T14 explicitly disables an account and revokes its sessions in separate SQL statements.
  Migration `0003_disabled_sessions` makes that transition atomic and rejects new live sessions for
  disabled owners, including writes outside the HTTP route.
- The reference relies on the unique parent index. This implementation keeps that index and also
  serializes mutations by family so a replay cannot miss a concurrently created descendant.
- The generic same-Idempotency-Key test conflicts with the cookie-only refresh contract. Twenty
  concurrent uses of one token create one child; the remaining requests detect reuse and revoke
  the family. Retrying a consumed token intentionally compromises the family rather than replaying 200.
- User notifications are excluded by the issue's explicit scope. `REFRESH` and `REUSE_DETECTED`
  audit events are persisted; no notification is sent.

This is off-chain authentication. Contract source remains pinned to
`1dc42d555046830c7320e46052696f62ab380dad`; deployment identity remains unverified. Refresh does not
change wallet authorization, transaction signing, coupon balances, or atomic contract settlement.

## HTTP behavior

Success returns exactly `accessToken`, `tokenType=Bearer`, and `expiresIn=900`. The JWT contains the
new session ID and a new `jti`, with issuer, audience, and the public-key ID from the ES256 signer.
The new refresh cookie has `HttpOnly; Secure; SameSite=Strict; Path=/v1/auth; Max-Age=2592000`.
Each successful rotation starts a new 30-day refresh lifetime, matching the reference service.

| HTTP | Code | Cookie behavior |
| --- | --- | --- |
| 401 | `REFRESH_TOKEN_INVALID` | Clear a missing, malformed, or unknown cookie. A JSON token is not used. |
| 401 | `REFRESH_TOKEN_EXPIRED` | Clear an unused token that has reached expiry. |
| 401 | `REFRESH_TOKEN_REVOKED` | Clear a revoked session, disabled-owner session, or changed identity binding. |
| 401 | `SESSION_COMPROMISED` | Clear a consumed token after committing family revocation and its audit event. |
| 503 | `AUTH_UNAVAILABLE` | Retain the cookie after a signer or database failure so an unused token can be retried. |

Errors contain a generated `requestId`. Responses use `Cache-Control: no-store` and `Pragma: no-cache`.
No response includes a plaintext refresh token in JSON or raw infrastructure errors.

## Atomic state transitions

The repository first reads and validates the stored session under the family lock. Signing occurs
outside the database transaction. Completion reacquires the same family lock and rechecks use,
revocation, expiry, owner identity, merchant binding, and current disability.

A successful transaction marks the parent used and revoked with reason `ROTATED`, inserts its
one child, and records `REFRESH`. Only after commit does HTTP return the JWT and cookie.
A signer failure leaves the parent unused. A database failure rolls back rotation and audit together.

Reusing a consumed token commits revocation of all currently unrevoked family members and records
`REUSE_DETECTED` before returning 401. Previously revoked rows keep their original reason.
Other families are unaffected. A descendant that was still signing must recheck state and cannot
create a new child after family revocation. A unique-parent violation from another writer is rolled
back and handled as reuse in a fresh transaction; unrelated integrity failures return sanitized 503.

PostgreSQL uses READ COMMITTED and transaction advisory locks per family. The current owner row
is locked while validating and committing. SQLite uses BEGIN IMMEDIATE. The database unique
indexes remain the final guard against duplicate refresh hashes and multiple children.

Rotated rows are retained for reuse detection. There is no cleanup job in this change. Deleting an
ancestor too early would turn a detected replay into an unknown token and lose family revocation.
Already-issued access JWTs remain cryptographically valid until expiry; refresh revocation does not
instantly revoke them. Sensitive operations still need their own current-account authorization.

## Disabled-owner migration

Migration `0003_disabled_sessions` backfills unrevoked sessions belonging to already-disabled
merchant-admins and customers. Updating `disabled_at` then revokes every unrevoked session for that
owner within the same transaction. Guards reject inserting, reassigning, or restoring an unrevoked
session for a disabled owner.

PostgreSQL guards take a shared lock on the owner row to coordinate raw inserts with concurrent
disability updates. SQLite serializes writers. Tests cover twenty session inserts racing account
disable, both owner types, migration backfill, repeated upgrade, drift checks, and downgrade/re-upgrade.
Downgrade removes the guards; it does not undo revocations or reactivate old sessions.

The normal migration command is `.venv/bin/alembic upgrade head`. Verification uses disposable
databases. No existing local development database or shared deployment was migrated by this task.

## Acceptance evidence

| Criterion | Proof |
| --- | --- |
| T9 | Expiry before or equal to creation violates `ck_expiry_after_create`. |
| T10 | Bcrypt and plaintext passwords violate `ck_admin_password_argon2id`. |
| T11 | Attempt success and failure code must agree. |
| T12 | Identifying attempt columns contain only hashes; HTTP code hashes peer IP and identifier. |
| T13 | Existing login tests prove five failed credentials cause a 900-second lockout without lost counts. |
| T14 | Disabling either owner type revokes all their families; database guards and concurrent-insert tests prevent live sessions for disabled owners. |

Additional tests cover real ES256 refresh JWTs, all four 401 errors, cookie clearing, signer and DB
rollback, refresh-hash collision, owner changes while signing, twenty concurrent reuses, and replay
while a descendant is signing. The reference pack remains unchanged.

Commands:

```bash
.venv/bin/python scripts/verify.py
docker compose up -d --wait postgres
export TEST_POSTGRES_URL='postgresql+psycopg://reward:local-development-only@127.0.0.1:5432/reward'
.venv/bin/pytest tests/integration/auth tests/integration/test_migrations.py -q
.venv/bin/python scripts/verify_login_api.py --newman newman --postgres
```

The Postman collection's `Fixture refresh` folder uses explicit cookies with its automatic cookie
jar disabled, so replay cases actually send the old token. The verifier checks each successful JWT
against public HTTP JWKS and verifies family revocation and signer-failure rollback in the database.
Fixture accounts, databases, and ephemeral PEMs are removed after the run.

Logout/session management remain in #179; signing-key rotation remains in #184. No shared staging
or production deployment is claimed.
