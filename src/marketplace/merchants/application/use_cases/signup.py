import json
from datetime import UTC, datetime
from hashlib import sha256

from marketplace.idempotency import fingerprint
from marketplace.merchants.application.exceptions import WalletProviderError
from marketplace.merchants.application.interfaces.signup import (
    SavedSignup,
    SignupCredentialPort,
    SignupRepository,
)
from marketplace.merchants.application.interfaces.wallets import WalletProvider
from marketplace.merchants.domain.entities.rate_limit import SignupRateLimit
from marketplace.merchants.domain.entities.signup import Signup
from marketplace.merchants.domain.exceptions import SignupFailure, SignupRateLimited


class SignupMerchant:
    def __init__(
        self,
        repository: SignupRepository,
        credentials: SignupCredentialPort,
        wallets: WalletProvider | None,
        limiter: SignupRateLimit,
    ) -> None:
        self.repository = repository
        self.credentials = credentials
        self.wallets = wallets
        self.limiter = limiter

    def execute(self, address: str, key: str, signup: Signup) -> str:
        retry_after = self.limiter.retry_after(address)
        if retry_after is not None:
            raise SignupRateLimited(retry_after)
        scope = "anon:" + sha256(signup.email_normalized.encode()).hexdigest()
        digest = fingerprint(
            {
                "name": signup.name,
                "email": signup.email_normalized,
                "displayName": signup.display_name,
            }
        )
        saved = self.repository.probe(scope, key, signup.email_normalized)
        if saved is not None:
            return self._replay(saved, digest, signup.password)
        if self.wallets is None:
            raise SignupFailure("WALLET_UNAVAILABLE")

        password_hash = self.credentials.hash(signup.password)
        request_ref = sha256(json.dumps([scope, key]).encode()).hexdigest()
        try:
            wallet = self.wallets.reserve(request_ref)
            if not wallet.is_valid():
                raise WalletProviderError
        except (WalletProviderError, OSError):
            raise SignupFailure("WALLET_UNAVAILABLE") from None
        result = self.repository.save(
            signup, password_hash, wallet, datetime.now(UTC), scope=scope, key=key, digest=digest
        )
        return result if isinstance(result, str) else self._replay(result, digest, signup.password)

    def _replay(self, saved: SavedSignup, digest: bytes, password: str) -> str:
        if saved.digest != digest:
            raise SignupFailure("IDEMPOTENCY_KEY_REUSED")
        if saved.status is None or saved.body is None:
            raise SignupFailure("IDEMPOTENCY_KEY_IN_PROGRESS")
        if saved.status != 201 or saved.password_hash is None:
            raise SignupFailure("SIGNUP_UNAVAILABLE")
        if not self.credentials.matches(password, saved.password_hash):
            raise SignupFailure("IDEMPOTENCY_KEY_REUSED")
        return saved.body
