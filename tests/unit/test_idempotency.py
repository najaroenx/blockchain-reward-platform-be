from marketplace.infrastructure.idempotency import canonical_json, fingerprint


def test_fingerprint_matches_spec_001_javascript_sha256_vector() -> None:
    # Generated independently with Node crypto + JSON.stringify(sorted keys).normalize('NFC').
    payload = {
        "chainId": "1337",
        "couponContractAddress": "0x" + "b" * 40,
        "walletAddress": "0x" + "a" * 40,
        "typeId": "1",
        "amount": "1",
    }
    assert (
        fingerprint(payload).hex()
        == "7f7a77175b618dbf95985179de6886a46c8e0444e972542e85a4aadbeb1ee759"
    )
    assert fingerprint(dict(reversed(list(payload.items())))) == fingerprint(payload)


def test_unicode_normalization_and_json_encoding_are_reproducible() -> None:
    assert canonical_json({"z": "e\u0301", "a": "1"}) == '{"a":"1","z":"é"}'
    assert fingerprint({"z": "e\u0301", "a": "1"}) == fingerprint({"a": "1", "z": "é"})
