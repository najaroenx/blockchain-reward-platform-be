# FEAT-03 Python schema foundation

This is the auth-only portion of issue #142, not the complete 14-pack schema merge.
The Python base at `73e3c8b` had catalog projections but no auth tables.

Owned by this foundation: Alembic migration `0002_auth_login`, the seven auth/identity ORM tables,
model registration, SQLite foreign-key enforcement, auth package boundaries, and disposable
PostgreSQL test databases. No login route, password verifier, JWT signing, refresh, or session API
is enabled by this commit.

The reduced `customer` table supplies session foreign keys only. The `counts_for_lockout` field
is a storage extension used by the login implementation; it does not implement lockout here.
Legacy SQL is not executed. This does not resolve schema reconciliation for all other packs,
customer OTP provisioning, or the separate 366-case harness in #143.

Schema acceptance and its T6/T7 checks belong to #176. Login belongs to #177, ES256 signing to
#182, and refresh rotation with disabled-owner guards to #178.

Source references are the previously verified FEAT-03 pack. Contract ABI source remains pinned
to `1dc42d555046830c7320e46052696f62ab380dad`; deployment is unverified and no chain writes are added.
