"""Fakes for the ports `AuthService` has no production implementation for this round.

`TokenSigner`/`TokenVerifier` are deliberately unimplemented — docs/architecture.md's
"Authentication" open decision defers the real (KMS-backed) signer. These stand in with a signer
that encodes the subject/session as a deterministic string, and a matching verifier.
"""

import pytest
from sqlalchemy.orm import Session

from marketplace.auth.application.exceptions import TokenVerificationError
from marketplace.auth.domain.entities import Subject
from marketplace.infrastructure.legacy_schema import merchant


class FakeTokenSigner:
    def sign(self, *, subject: Subject, session_id: str) -> str:
        return f"{subject.kind}:{subject.id}:{session_id}"


class FakeTokenVerifier:
    def verify(self, access_token: str) -> tuple[Subject, str]:
        parts = access_token.split(":")
        if len(parts) != 3:
            raise TokenVerificationError
        kind, subject_id, session_id = parts
        return Subject(kind=kind, id=subject_id), session_id  # type: ignore[arg-type]


@pytest.fixture
def signer() -> FakeTokenSigner:
    return FakeTokenSigner()


@pytest.fixture
def verifier() -> FakeTokenVerifier:
    return FakeTokenVerifier()


@pytest.fixture
def seed_merchant():
    def _seed(session: Session, merchant_id: str = "mch_1") -> None:
        session.execute(merchant.insert().values(id=merchant_id, name="Test Merchant"))

    return _seed
