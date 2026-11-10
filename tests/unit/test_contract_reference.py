import hashlib
import json
from importlib.resources import files


def test_reference_assets_match_the_recorded_checksums() -> None:
    root = files("marketplace.infrastructure.blockchain") / "abi"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["deployment_status"] == "unverified"
    assert manifest["revision"] == "1dc42d555046830c7320e46052696f62ab380dad"
    for name, checksum in manifest["contracts"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == checksum


def test_contract_shapes_preserve_the_actual_callers_and_outputs() -> None:
    root = files("marketplace.infrastructure.blockchain") / "abi"
    market = json.loads((root / "MarketplaceUpgradeable.json").read_text())
    coupon = json.loads((root / "Coupon.json").read_text())
    functions = {entry["name"]: entry for entry in market if entry["type"] == "function"}
    assert [item["name"] for item in functions["buyCoupon"]["inputs"]] == ["listingId", "amount"]
    assert functions["buyCoupon"]["outputs"] == []
    assert [item["name"] for item in functions["refundExpiredCoupon"]["inputs"]] == [
        "typeId",
        "maxEscrows",
    ]
    assert functions["refundExpiredCoupon"]["outputs"][0]["name"] == "remaining"
    redeem = next(entry for entry in coupon if entry.get("name") == "redeem")
    assert redeem["outputs"][0]["type"] == "string[]"
