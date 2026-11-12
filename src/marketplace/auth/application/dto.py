from dataclasses import dataclass
from datetime import datetime

from marketplace.auth.domain.entities import Subject, SubjectKind


@dataclass(frozen=True)
class StoredSession:
    """The subset of an `auth_session` row the application layer needs.

    Returned by `AuthSessionStore`, never an ORM instance directly — keeps `application/` free of
    SQLAlchemy per this project's layering (see docs/architecture.md).
    """

    id: str
    subject_kind: SubjectKind
    merchant_admin_id: str | None
    family_id: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class LoginResult:
    subject: Subject
    merchant_status: str
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class RefreshResult:
    subject: Subject
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    current: bool
    created_at: str
    expires_at: str
