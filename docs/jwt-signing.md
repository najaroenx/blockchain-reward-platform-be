# ES256 access-token signing

Issue [#182](https://github.com/najaroenx/blockchain-reward-platform/issues/182) adds real signing
to merchant login. The selected policy is ES256 with a P-256 PEM private key mounted by the
deployment's secrets system. `Es256TokenSigner` loads that file once at API startup. The API keeps
the parsed key in memory and never writes it to the database or response.

## Source reconciliation

- The issue names TypeScript files. This Python implementation uses
  `auth/infrastructure/signing.py` and `auth/presentation/jwks.py`.
- The FEAT-03 reference service delegates `iat` and `exp` to its signer. The existing Python
  `Login` use case already supplies both with a 900-second lifetime. The adapter preserves them
  and adds the configured `iss` and `aud`.
- Reference `types.ts` includes `mid` and `sid`, while the OpenAPI prose lists fewer claims.
  Issued tokens preserve the current merchant login claims: `sub`, `kind`, `mid`, `sid`, `jti`,
  `iat`, and `exp`. They contain no roles, permissions, password, or refresh token.
- JWT key material is separate from the worker's transaction-signing key. API configuration
  continues to reject worker KMS settings. Worker and indexer reject JWT key settings.
- The issue's generic duplicate Idempotency-Key test does not match login's API contract.
  Twenty independent logins create twenty separately signed sessions. No signing endpoint is added.
- The referenced `.github/DEFINITION_OF_DONE.md` was unavailable in the issue repository when checked.
  The four explicit acceptance criteria are mapped below.

Contract source remains pinned to `1dc42d555046830c7320e46052696f62ab380dad`. In
`contracts/abstracts/BaseCoupon.sol:121` and `:160`, authorization still depends on the transaction
caller and ERC-1155 approval. A JWT does not establish wallet ownership or authorize a transaction.
Deployment identity remains unverified. This off-chain feature does not depend on a deployment.

## Configuration

| API setting | Value |
| --- | --- |
| `JWT_PRIVATE_KEY_FILE` | Absolute path to a mounted, unencrypted P-256 private PEM file. |
| `JWT_ISSUER` | Exact HTTP(S) issuer string. Production requires HTTPS. Trailing slashes are preserved. |
| `JWT_AUDIENCE` | Exact nonempty audience without whitespace. |

All three settings form one configuration. A partial configuration is rejected with a sanitized
error. Missing, unreadable, malformed, encrypted, public-only, RSA, or other-curve files fail startup.
No key is generated automatically by the normal API process.

In development and test, omitting all three leaves signing disabled. Login with valid credentials
and the JWKS route return `503 AUTH_UNAVAILABLE`. The normal production entry point requires
PostgreSQL and complete JWT signing configuration.

`.env.api.example` contains commented configuration examples. The secrets provider and production
issuer/audience values are deployment inputs; this change does not provision a cloud secret.
File access controls and delivery are managed by that secrets system. No PEM fixture is committed.

## JWT and public key contract

The JOSE header contains `alg=ES256`, `typ=JWT`, and `kid`. The key ID is the SHA-256
[RFC 7638 JWK thumbprint](https://www.rfc-editor.org/rfc/rfc7638), encoded as unpadded base64url.
It remains stable for the same public key and changes when the key changes.

`GET /.well-known/jwks.json` is public and returns one key with exactly these fields:
`kid`, `kty=EC`, `crv=P-256`, `alg=ES256`, `use=sig`, `x`, and `y`. It exposes no private scalar,
PEM contents, or secret-file path. Successful responses use `Cache-Control: public, max-age=60`.
Unavailable responses use the auth error envelope and `Cache-Control: no-store`.

Consumers select a trusted published key by `kid`, allow only ES256, and validate the configured
issuer, audience, expiry, and required claims. They do not derive trusted key URLs or allowed
algorithms from an unverified token. The signer uses [PyJWT's ES256 support](https://pyjwt.readthedocs.io/en/stable/usage.html)
with [cryptography's P-256 key handling](https://cryptography.io/en/46.0.7/hazmat/primitives/asymmetric/ec/).
This issue publishes verification keys; protected-route guards remain with their API owners.

Login signs before opening the session transaction. A signing failure maps to
`503 AUTH_UNAVAILABLE`, records an unsuccessful attempt, and creates no session or cookie.
Database commit failure likewise returns no access token or cookie to the caller.

## Rotation boundary

This implementation publishes the boot-loaded key. Replacing the mounted file does not change
the running process's signer or JWKS. Multi-key overlap, active-key promotion, and retirement
belong to [#184](https://github.com/najaroenx/blockchain-reward-platform/issues/184).
Restarting with a new key before that overlap is implemented removes the old verification key
from JWKS and can invalidate clients' ability to verify still-live tokens.

Refresh rotation is implemented in [#178](auth-refresh.md); logout remains in #179. No shared staging or production deployment,
cloud-secret integration, or completed key rotation is claimed.

## Acceptance evidence and commands

| #182 acceptance | Evidence |
| --- | --- |
| Real integration | Login returns an ES256 JWT verified using only the key fetched from HTTP JWKS. Independent cryptography verification checks the JWS signature bytes. Twenty concurrent logins produce distinct `jti` and session IDs. |
| Rejections | Invalid key/configuration fails without secret echo. Runtime signing failure returns the auth 503 envelope without a session. Consumer checks reject tampering, expiry, wrong issuer/audience, and an alternate algorithm. |
| Added acceptance coverage | `tests/unit/test_jwt_signing.py`, `tests/integration/auth/test_jwt.py`, and JWT config tests in `tests/unit/test_settings.py`. |
| OpenAPI | The generated login specification includes JWKS; schema validation and artifact drift checks run in `scripts/verify.py`. |

Verification commands:

```bash
.venv/bin/python scripts/verify.py
.venv/bin/python scripts/verify_login_api.py --newman newman
```

The 2026-11-13 run passed 109 tests and all quality checks. PostgreSQL auth/migration checks
passed 38 tests. Newman passed 26 requests and 107 assertions on both SQLite and PostgreSQL,
including verification of the successful token against the public HTTP JWKS.

PostgreSQL verification commands create disposable databases on the supplied local server:

```bash
docker compose up -d --wait postgres
export TEST_POSTGRES_URL='postgresql+psycopg://reward:local-development-only@127.0.0.1:5432/reward'
.venv/bin/pytest tests/integration/auth tests/integration/test_migrations.py -q
.venv/bin/python scripts/verify_login_api.py --newman newman --postgres
```

The Newman verifier generates an ephemeral PEM key, seeds merchant accounts, and starts an HTTP
app. It verifies successful JWTs against the HTTP JWKS using fixed issuer/audience expectations.
One seeded account deliberately injects a signing failure. Databases and key files are removed
after the run. OpenAPI and Postman artifacts are regenerated with `--export`.
