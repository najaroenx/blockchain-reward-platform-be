# Auth issue ownership after splitting BE-0306

Issue numbers refer to the [planning repository](https://github.com/najaroenx/blockchain-reward-platform/issues).
All work starts from `recreate/python-backend` at `73e3c8b`. Each branch contains its parent
dependencies and one issue-specific commit. PRs compare against the immediate parent, so their
diffs show only that issue's work.

| Branch | Parent and PR base | Issue and owned work | Full local suite |
| --- | --- | --- | --- |
| `BE-0002-feat03` | `recreate/python-backend` | #142, auth-only schema foundation, ORM registration, SQLite foreign keys and disposable PostgreSQL fixtures | 41 passed |
| `BE-0305` | `BE-0002-feat03` | #176, database-only FEAT-03 acceptance including T6/T7 | 53 passed |
| `BE-0306` | `BE-0305` | #177, login, Argon2id, attempts, lockout, rate limit and session admission | 75 passed |
| `BE-0393` | `BE-0306` | #182, ES256 PEM signing, issuer/audience configuration and JWKS | 112 passed |
| `BE-0307` | `BE-0393` | #178, refresh rotation, reuse revocation and disabled-owner guards | 149 passed |

`BE-0002-feat03` is only the auth portion extracted from the login branch. It does not complete
the full 14-pack schema merge in #142. The full 366-case harness in #143 is also outside this split.
The reduced customer table remains a session-owner foundation, not customer OTP implementation.

Schema acceptance does not import login or refresh repositories. T3 checks the database family
update directly. The refresh issue separately proves HTTP replay and descendant revocation.
T6 adds both mismatched-owner cases; T7 asserts that the session's only token column is its hash.
Those three new cases explain the increase from the previous final suite of 146 to 149 tests.

## Source and preservation

- Original `84a7275` was split into schema foundation, schema acceptance and login commits.
- Signer work was cherry-picked from `7254056` onto the corrected login dependency.
- Refresh work was checkpointed as `4288235` and cherry-picked onto the corrected signer dependency.
- Runtime source, migrations, verification scripts, dependency configuration and lockfile on the
  final branch match `4288235` byte for byte. The changes to the final tree are ownership documentation
  and schema-test organization/coverage.

The original commits remain under these archive refs:

```text
archive/BE-0306-before-split-20261113
archive/BE-0393-before-split-20261113
archive/BE-0307-before-split-20261113
```

The split was built and checked in an isolated worktree with `PYTHONPATH` pointing to its own
`src` directory. The original workspace and its local environment files were retained.
Each stage passed Ruff, formatting, mypy, import boundaries and its full pytest suite before
the next feature was cherry-picked. API artifact validation runs from the login stage onward.
The final auth/migration suite also passed 78 tests on PostgreSQL. Generated OpenAPI and Postman
files match the original verified snapshot as well as the regenerated runtime specification.

Review commands:

```bash
git diff BE-0305...BE-0306 --stat
git diff BE-0306...BE-0393 --stat
git diff BE-0393...BE-0307 --stat
git diff 4288235 BE-0307 -- src migrations scripts pyproject.toml uv.lock
```

The final command produces no diff. Public API behavior, transaction semantics and key policy
were preserved. No chain deployment, shared staging deployment, or completion of the larger
schema/harness issues is implied by this ownership split.

## Pull requests

- [#5: auth schema foundation](https://github.com/najaroenx/blockchain-reward-platform-be/pull/5), draft because it covers only the auth portion of #142.
- [#6: schema acceptance](https://github.com/najaroenx/blockchain-reward-platform-be/pull/6), based on the foundation branch.
- [#4: login](https://github.com/najaroenx/blockchain-reward-platform-be/pull/4), retargeted to the acceptance branch.
- [#7: signer](https://github.com/najaroenx/blockchain-reward-platform-be/pull/7), based on login.
- [#8: refresh](https://github.com/najaroenx/blockchain-reward-platform-be/pull/8), based on the signer.
