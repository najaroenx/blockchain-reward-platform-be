import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self, overload
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

Role = Literal["api", "worker", "indexer"]
KMS_FIELDS = ("KMS_KEY_ARN", "KMS_REGION")
JWT_FIELDS = ("JWT_PRIVATE_KEY_FILE", "JWT_ISSUER", "JWT_AUDIENCE")


class ConfigurationError(ValueError):
    """A configuration error whose message never includes input values."""


class SharedSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr
    log_level: Literal["trace", "debug", "info", "warn", "error", "fatal"] = "info"
    environment: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="APP_ENV"
    )

    @field_validator("database_url")
    @classmethod
    def supported_database(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except ArgumentError:
            raise ValueError("must be a PostgreSQL or SQLite URL") from None
        if url.drivername not in {"postgresql", "postgresql+psycopg", "sqlite"}:
            raise ValueError("must use postgresql+psycopg or sqlite")
        if url.get_backend_name() == "postgresql" and (not url.host or not url.database):
            raise ValueError("PostgreSQL URL requires a host and database")
        if url.get_backend_name() == "sqlite" and not url.database:
            raise ValueError("SQLite URL requires a database path or :memory:")
        return value


class JwtSigningSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    private_key_file: SecretStr
    issuer: str = Field(min_length=1, max_length=2048)
    audience: str = Field(min_length=1, max_length=256)

    @field_validator("private_key_file")
    @classmethod
    def absolute_key_path(cls, value: SecretStr) -> SecretStr:
        if not Path(value.get_secret_value()).is_absolute():
            raise ValueError("requires an absolute secret-file path")
        return value

    @field_validator("issuer")
    @classmethod
    def valid_issuer(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(character.isspace() for character in value)
        ):
            raise ValueError("requires an HTTP(S) issuer without credentials, query or fragment")
        return value

    @field_validator("audience")
    @classmethod
    def valid_audience(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("audience must not contain whitespace")
        return value


class ApiSettings(SharedSettings):
    host: str = "127.0.0.1"
    port: int = Field(default=3000, ge=1, le=65535)
    # Default off: the coupon-types write endpoint persists locally only (no deployment,
    # signing, or auth decision exists yet to submit createCouponType on-chain). See
    # docs/architecture.md "Decisions still required".
    coupon_types_enabled: bool = False
    mock_auth_enabled: bool = False
    mock_deposit_psp_enabled: bool = False
    mock_merchant_wallet_enabled: bool = False
    merchant_signup_per_minute: int = Field(default=30, ge=1, le=1000)
    mock_auth_token: SecretStr = Field(default=SecretStr("mock-redeem-token"), min_length=1)
    mock_auth_user_id: str = Field(default="mock-user", min_length=1)
    mock_auth_wallet_address: str = Field(default="0x" + "a" * 40, pattern=r"^0x[0-9a-fA-F]{40}$")
    # Verifies redeem's incoming access tokens (BE-0608). Shares JWT_ISSUER/JWT_AUDIENCE
    # with jwt_signing below, which mints them (#182) -- same issuer identity, different
    # (public vs private) key.
    jwt_public_key_file: Path | None = None
    jwt_issuer: str | None = Field(default=None, min_length=1)
    jwt_audience: str | None = Field(default=None, min_length=1)
    login_ip_limit: int = Field(default=20, ge=1)
    login_ip_window_seconds: int = Field(default=60, ge=1)
    jwt_signing: JwtSigningSettings | None = None

    @model_validator(mode="before")
    @classmethod
    def collect_jwt_settings(cls, values: object) -> object:
        if not isinstance(values, dict) or "jwt_private_key_file" not in values:
            return values
        result = dict(values)
        if result.get("jwt_signing") is not None:
            raise ValueError("configure JWT signing once")
        # issuer/audience are read, not popped: redeem's flat jwt_issuer/jwt_audience
        # fields above consume the same JWT_ISSUER/JWT_AUDIENCE input.
        result["jwt_signing"] = {
            "private_key_file": result.pop("jwt_private_key_file"),
            "issuer": result.get("jwt_issuer"),
            "audience": result.get("jwt_audience"),
        }
        return result

    @model_validator(mode="after")
    def jwt_verification_configuration(self) -> Self:
        if self.mock_deposit_psp_enabled and self.environment == "production":
            raise ValueError("mock deposit payments are not allowed in production")
        if self.mock_merchant_wallet_enabled and self.environment == "production":
            raise ValueError("mock merchant wallets are not allowed in production")
        # jwt_public_key_file is the sole trigger, not "any of the three fields set":
        # jwt_issuer/jwt_audience are shared with jwt_signing above, so a signing-only
        # setup legitimately has them set without a public key file.
        if self.jwt_public_key_file is None:
            return self
        if self.jwt_issuer is None or self.jwt_audience is None:
            raise ValueError("JWT verification requires public key file, issuer, and audience")
        if self.mock_auth_enabled:
            raise ValueError("JWT verification and mock authentication cannot be enabled together")
        return self

    @field_validator("mock_auth_enabled")
    @classmethod
    def mock_auth_is_local(cls, value: bool, info: ValidationInfo) -> bool:
        if value and info.data.get("environment") == "production":
            raise ValueError("mock authentication is not allowed in production")
        return value


class ChainSettings(SharedSettings):
    rpc_url: SecretStr
    chain_id: int = Field(gt=0)

    @field_validator("rpc_url")
    @classmethod
    def valid_rpc(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("must be an HTTP(S) RPC URL")
        return value


class WorkerSettings(ChainSettings):
    kms_key_arn: SecretStr = Field(min_length=1)
    kms_region: str = Field(min_length=1)


class IndexerSettings(ChainSettings):
    finality_depth: int = Field(default=12, ge=1)


def find_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return current


@overload
def load_settings(
    role: Literal["api"], *, root: Path | None = None, environ: Mapping[str, str] | None = None
) -> ApiSettings: ...


@overload
def load_settings(
    role: Literal["worker"], *, root: Path | None = None, environ: Mapping[str, str] | None = None
) -> WorkerSettings: ...


@overload
def load_settings(
    role: Literal["indexer"], *, root: Path | None = None, environ: Mapping[str, str] | None = None
) -> IndexerSettings: ...


def load_settings(
    role: Role, *, root: Path | None = None, environ: Mapping[str, str] | None = None
) -> ApiSettings | WorkerSettings | IndexerSettings:
    directory = root or find_root()
    values: dict[str, str] = {}
    for name in (".env", f".env.{role}"):
        values.update(
            {
                key.upper(): value
                for key, value in dotenv_values(directory / name, interpolate=False).items()
                if value is not None
            }
        )
    values.update(
        {key.upper(): value for key, value in (os.environ if environ is None else environ).items()}
    )
    if role != "worker":
        forbidden = [key for key in KMS_FIELDS if values.get(key)]
        if forbidden:
            raise ConfigurationError(
                f"{role}: worker-only variables present: {', '.join(forbidden)}"
            )
    if role != "api":
        forbidden = [key for key in JWT_FIELDS if key in values]
        if forbidden:
            raise ConfigurationError(f"{role}: api-only variables present: {', '.join(forbidden)}")
    schemas: dict[Role, type[ApiSettings] | type[WorkerSettings] | type[IndexerSettings]] = {
        "api": ApiSettings,
        "worker": WorkerSettings,
        "indexer": IndexerSettings,
    }
    schema = schemas[role]
    try:
        # Validate the role-specific merge without reading the global environment again.
        settings = schema.model_validate(
            {key.lower() if key != "APP_ENV" else key: value for key, value in values.items()}
        )
    except ValidationError as error:
        fields = sorted({".".join(map(str, item["loc"])) for item in error.errors()})
        raise ConfigurationError(
            f"{role}: invalid or missing configuration: {', '.join(fields)}"
        ) from None
    if (
        settings.environment == "production"
        and make_url(settings.database_url.get_secret_value()).get_backend_name() != "postgresql"
    ):
        raise ConfigurationError(f"{role}: production requires PostgreSQL")
    if isinstance(settings, ApiSettings) and settings.environment == "production":
        if settings.jwt_signing is None:
            raise ConfigurationError("api: production requires JWT signing configuration")
        if not settings.jwt_signing.issuer.startswith("https://"):
            raise ConfigurationError("api: production JWT issuer requires HTTPS")
    return settings


def init_env() -> None:
    root = find_root()
    for name in (".env", ".env.api", ".env.worker", ".env.indexer"):
        example = root / f"{name}.example"
        target = root / name
        if not example.exists() or target.exists():
            continue
        try:
            with target.open("x") as output:
                output.write(example.read_text())
            target.chmod(0o600)
            print(f"Created {name}")
        except FileExistsError:
            continue
