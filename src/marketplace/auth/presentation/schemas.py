from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)


class SubjectResponse(BaseModel):
    kind: str
    id: str
    merchant_id: str | None = None
    merchant_status: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    subject: SubjectResponse


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class SessionView(BaseModel):
    session_id: str
    current: bool
    created_at: str
    expires_at: str


class SessionsResponse(BaseModel):
    sessions: list[SessionView]


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
