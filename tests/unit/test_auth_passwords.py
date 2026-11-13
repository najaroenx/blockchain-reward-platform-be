from unittest.mock import patch

from argon2 import PasswordHasher, extract_parameters

from marketplace.auth.infrastructure.passwords import Argon2PasswordVerifier


def test_missing_invalid_and_wrong_passwords_each_run_argon2_verification() -> None:
    hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    verifier = Argon2PasswordVerifier(hasher)
    encoded = hasher.hash("real-password")
    original = PasswordHasher.verify
    hashes = []

    def observe(self, encoded_hash, password):
        hashes.append(encoded_hash)
        return original(self, encoded_hash, password)

    with patch.object(PasswordHasher, "verify", observe):
        assert verifier.verify(None, "wrong") is False
        assert verifier.verify("$argon2id$broken", "wrong") is False
        assert verifier.verify(encoded, "wrong") is False
        assert verifier.verify(encoded, "real-password") is True
        assert verifier.verify(None, "dummy-login-verification") is False
    assert len(hashes) == 5
    assert all(extract_parameters(value) == extract_parameters(encoded) for value in hashes)


def test_different_costs_do_not_create_a_faster_unknown_account_path() -> None:
    policy = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    weak = PasswordHasher(time_cost=1, memory_cost=512, parallelism=1).hash("password")
    verifier = Argon2PasswordVerifier(policy)
    assert verifier.verify(weak, "password") is False
