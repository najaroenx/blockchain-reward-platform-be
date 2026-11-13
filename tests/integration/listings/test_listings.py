from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


def seed(client: TestClient) -> None:
    with client.app.state.database.sessions.begin() as session:
        for chain_id, market in ((1337, MARKET), (1337, OTHER), (1, MARKET)):
            session.add(
                ProjectionCheckpoint(
                    chain_id=chain_id, marketplace_address=market, indexed_block=42
                )
            )
            for listing_id, active in (("1", True), ("2", False), ("3", True)):
                session.add(
                    ListingProjection(
                        chain_id=chain_id,
                        marketplace_address=market,
                        listing_id=listing_id,
                        seller=SELLER,
                        type_id=str(2**256 - 1),
                        amount="5",
                        price_per_unit=str(2**256 - 1),
                        payment_token=PAYMENT,
                        active=active,
                        listed_at=1700000000 + int(listing_id),
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
