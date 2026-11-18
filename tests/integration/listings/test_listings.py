from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from marketplace.listings.infrastructure import repository
from marketplace.listings.infrastructure.models import (
    ListingProjection,
    ProjectionCheckpoint,
)
from marketplace.main import create_app
from marketplace.settings import ApiSettings

MARKET = "0x" + "a" * 40
OTHER = "0x" + "b" * 40
SELLER = "0x" + "c" * 40
PAYMENT = "0x" + "d" * 40
URL = f"/api/v1/marketplaces/1337/{MARKET}/listings"
MAX_UINT256 = 2**256 - 1


def seed(client: TestClient, *, newest_listing_expiry: int | None = MAX_UINT256) -> None:
    with client.app.state.database.sessions.begin() as session:
        for chain_id, market in ((1337, MARKET), (1337, OTHER), (1, MARKET)):
            session.add(
                ProjectionCheckpoint(
                    chain_id=chain_id, marketplace_address=market, indexed_block=42
                )
            )
            for listing_id, active in (("1", True), ("2", False), ("3", True)):
                newest = listing_id == "3"
                session.add(
                    ListingProjection(
                        chain_id=chain_id,
                        marketplace_address=market,
                        listing_id=listing_id,
                        seller=SELLER,
                        type_id=str(MAX_UINT256 if newest else MAX_UINT256 - 1),
                        amount="5",
                        price_per_unit=str(2**256 - 1),
                        payment_token=PAYMENT,
                        active=active,
                        listed_at=1700000000 + int(listing_id),
                        coupon_expires_at=newest_listing_expiry if newest else MAX_UINT256,
                    )
                )


def test_health_compatibility_and_database_readiness(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"ok": True, "role": "api"}
    assert client.get("/readyz").json() == {"ok": True, "database": "ready"}


def test_unindexed_marketplace_is_not_reported_as_an_empty_market(client: TestClient) -> None:
    response = client.get(URL)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "projection_unavailable"


def test_catalog_is_scoped_filtered_and_paginated_without_losing_uint256(
    client: TestClient,
) -> None:
    seed(client)
    result = client.get(URL, params={"limit": 1}).json()
    assert result["total"] == 2
    assert result["indexed_block"] == 42
    assert [item["listing_id"] for item in result["items"]] == ["3"]
    assert result["items"][0]["price_per_unit"] == str(2**256 - 1)
    assert result["items"][0]["type_id"] == str(2**256 - 1)
    result = client.get(URL, params={"limit": 1, "offset": 1}).json()
    assert [item["listing_id"] for item in result["items"]] == ["1"]


def test_detail_distinguishes_missing_and_inactive_listings(client: TestClient) -> None:
    seed(client)
    assert client.get(f"{URL}/999").status_code == 404
    inactive = client.get(f"{URL}/2")
    assert inactive.status_code == 200
    assert inactive.json()["listing"]["active"] is False


def test_catalog_expires_a_listing_without_writing_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    expiry = 1_700_000_010
    seed(client, newest_listing_expiry=expiry)
    now = expiry + 0.9
    monkeypatch.setattr(repository, "time", lambda: now)

    at_expiry = client.get(URL, params={"limit": 1}).json()
    assert at_expiry["total"] == 2
    assert [item["listing_id"] for item in at_expiry["items"]] == ["3"]
    assert client.get(f"{URL}/3").json()["listing"]["active"] is True

    now = expiry + 1.0
    after_expiry = client.get(URL, params={"limit": 1}).json()
    assert after_expiry["total"] == 1
    assert [item["listing_id"] for item in after_expiry["items"]] == ["1"]
    empty_page = client.get(URL, params={"offset": 1}).json()
    assert empty_page["items"] == []
    assert empty_page["total"] == 1
    expired = client.get(f"{URL}/3")
    assert expired.status_code == 200
    assert expired.json()["listing"]["active"] is False

    with client.app.state.database.sessions() as session:
        row = session.get_one(ListingProjection, (1337, MARKET, "3"))
        assert (row.active, row.coupon_expires_at) == (True, expiry)


def test_catalog_excludes_a_listing_with_unknown_coupon_expiry(client: TestClient) -> None:
    seed(client, newest_listing_expiry=None)

    response = client.get(URL).json()
    assert response["total"] == 1
    assert [item["listing_id"] for item in response["items"]] == ["1"]
    detail = client.get(f"{URL}/3")
    assert detail.status_code == 200
    assert detail.json()["listing"]["active"] is False


def test_addresses_are_case_insensitive(client: TestClient) -> None:
    seed(client)
    response = client.get(URL.replace(MARKET, "0x" + "A" * 40))
    assert response.status_code == 200
    assert response.json()["total"] == 2


@pytest.mark.parametrize(
    "suffix", ["?limit=0", "?limit=101", "?offset=-1", "/-1", "/01", f"/{2**256}"]
)
def test_input_boundaries(client: TestClient, suffix: str) -> None:
    assert client.get(URL + suffix).status_code == 422


def test_invalid_address_is_rejected(client: TestClient) -> None:
    assert client.get(URL.replace(MARKET, "not-an-address")).status_code == 422


def test_catalog_has_no_write_endpoint(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path, route in paths.items():
        if not path.startswith("/api/v1/marketplaces/"):
            continue
        assert not set(route) & {"post", "put", "patch", "delete"}


def test_rolled_back_projection_is_not_visible(client: TestClient) -> None:
    with client.app.state.database.sessions() as session:
        session.add(
            ProjectionCheckpoint(chain_id=1337, marketplace_address=MARKET, indexed_block=42)
        )
        session.flush()
        session.rollback()
    assert client.get(URL).status_code == 503


def test_liveness_survives_database_failure_and_readiness_fails(tmp_path: Path) -> None:
    missing = tmp_path / "missing-directory" / "db.sqlite"
    with TestClient(create_app(ApiSettings(database_url=f"sqlite:///{missing}"))) as http:
        assert http.get("/healthz").status_code == 200
        response = http.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"ok": False, "database": "unavailable"}
        catalog = http.get(URL)
        assert catalog.status_code == 503
        assert str(missing) not in catalog.text
