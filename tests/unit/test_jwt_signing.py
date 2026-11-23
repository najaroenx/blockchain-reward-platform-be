import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from marketplace.auth.application.dto import AccessClaims
from marketplace.auth.application.exceptions import AuthFailure
from marketplace.auth.infrastructure.signing import Es256TokenSigner, SigningConfigurationError

ISSUER = "https://auth.example.test"
AUDIENCE = "reward-api"


def claims() -> AccessClaims:
    now = int(datetime.now(UTC).timestamp())
    return AccessClaims(
        sub="adm_test", mid="mch_test", sid="ses_test", jti="unique", iat=now, exp=now + 900
    )


def signer(path: Path) -> Es256TokenSigner:
    return Es256TokenSigner(path, issuer=ISSUER, audience=AUDIENCE)


def test_token_is_es256_and_verifiable_using_only_public_jwk(jwt_key_file: Path) -> None:
    signing = signer(jwt_key_file)
    access = claims()
    token = signing.sign(access)
    public = asdict(signing.public_keys()[0])
    assert set(public) == {"kid", "kty", "crv", "alg", "use", "x", "y"}
    assert jwt.get_unverified_header(token) == {"kid": public["kid"], "typ": "JWT", "alg": "ES256"}
    key = jwt.PyJWK.from_dict(public).key
    decoded = jwt.decode(token, key, algorithms=["ES256"], issuer=ISSUER, audience=AUDIENCE)
    assert decoded == {**asdict(access), "iss": ISSUER, "aud": AUDIENCE}
    assert decoded["exp"] - decoded["iat"] == 900
    assert not {"role", "permission", "password", "refreshToken"} & decoded.keys()
    # Verify the actual JWS bytes independently of PyJWT's decoder.
    header, payload, signature = token.split(".")
    raw = jwt.utils.base64url_decode(signature)
    assert len(raw) == 64
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.verify(der, (header + "." + payload).encode(), ec.ECDSA(hashes.SHA256()))


def test_kid_is_stable_for_one_key_and_changes_for_a_different_key(jwt_key_file: Path) -> None:
    first = signer(jwt_key_file).public_keys()[0]
    assert signer(jwt_key_file).public_keys()[0] == first
    jwt_key_file.write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
    )
    assert signer(jwt_key_file).public_keys()[0].kid != first.kid


def test_key_is_loaded_at_boot_and_not_re_read_per_token(jwt_key_file: Path) -> None:
    signing = signer(jwt_key_file)
    jwt_key_file.unlink()
    key = jwt.PyJWK.from_dict(asdict(signing.public_keys()[0])).key
    jwt.decode(signing.sign(claims()), key, algorithms=["ES256"], issuer=ISSUER, audience=AUDIENCE)


@pytest.mark.parametrize(
    "case", ["missing", "garbage", "public", "encrypted", "rsa", "p384", "k256", "ed25519"]
)
def test_invalid_signing_keys_fail_without_exposing_material(tmp_path: Path, case: str) -> None:
    path = tmp_path / "SECRET-key.pem"
    key = ec.generate_private_key(ec.SECP256R1())
    if case == "garbage":
        path.write_bytes(b"SECRET private-key material")
    elif case == "public":
        path.write_bytes(
            key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        )
    elif case != "missing":
        if case == "rsa":
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        elif case == "p384":
            key = ec.generate_private_key(ec.SECP384R1())
        elif case == "k256":
            key = ec.generate_private_key(ec.SECP256K1())
        elif case == "ed25519":
            key = ed25519.Ed25519PrivateKey.generate()
        encryption = BestAvailableEncryption(b"SECRET") if case == "encrypted" else NoEncryption()
        path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, encryption))
    with pytest.raises(SigningConfigurationError) as result:
        signer(path)
    assert "SECRET" not in str(result.value)
    assert str(path) not in str(result.value)


@pytest.mark.parametrize("case", ["issuer", "audience", "expired", "tampered", "algorithm"])
def test_public_key_consumer_rejects_invalid_tokens(jwt_key_file: Path, case: str) -> None:
    signing = signer(jwt_key_file)
    token = signing.sign(claims())
    public = jwt.PyJWK.from_dict(asdict(signing.public_keys()[0])).key
    if case == "expired":
        access = claims()
        token = signing.sign(
            AccessClaims(
                sub=access.sub, mid=access.mid, sid=access.sid, jti=access.jti, iat=1, exp=901
            )
        )
    elif case == "tampered":
        header, payload, signature = token.split(".")
        data = json.loads(jwt.utils.base64url_decode(payload))
        data["mid"] = "mch_attacker"
        token = ".".join(
            [header, jwt.utils.base64url_encode(json.dumps(data).encode()).decode(), signature]
        )
    elif case == "algorithm":
        token = jwt.encode(
            {**asdict(claims()), "iss": ISSUER, "aud": AUDIENCE},
            "fixture-secret-32-characters-long",
            algorithm="HS256",
        )
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(
            token,
            public,
            algorithms=["ES256"],
            issuer="https://wrong.test" if case == "issuer" else ISSUER,
            audience="other-api" if case == "audience" else AUDIENCE,
        )


def test_signing_library_failure_is_sanitized(
    jwt_key_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args, **kwargs):
        raise jwt.InvalidKeyError("SECRET provider details")

    monkeypatch.setattr(jwt, "encode", fail)
    with pytest.raises(AuthFailure) as result:
        signer(jwt_key_file).sign(claims())
    assert result.value.refusal.code == "AUTH_UNAVAILABLE"
    assert "SECRET" not in str(result.value)
