from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from marketplace.main import create_app
from marketplace.settings import ApiSettings

URL = "/api/v1/coupon-types"


def body(**overrides: object) -> dict[object, object]:
    payload: dict[object, object] = {
        "name": "Summer Promo",
        "start_date": 1_700_000_000,
        "expire_date": 1_800_000_000,
        "face_value": 100,
        "total_supply": 500,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    settings = ApiSettings(database_url=database_url, APP_ENV="test", coupon_types_enabled=True)
    with TestClient(create_app(settings)) as http:
        yield http


def test_feature_flag_disabled_by_default(database_url: str) -> None:
    settings = ApiSettings(database_url=database_url, APP_ENV="test")
    with TestClient(create_app(settings)) as http:
        assert http.get(URL).status_code == 404
        assert http.post(URL, json=body(), headers={"Idempotency-Key": "k"}).status_code == 404


def test_create_persists_without_touching_chain(client: TestClient) -> None:
    response = client.post(URL, json=body(), headers={"Idempotency-Key": "key-1"})
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Summer Promo"
    assert created["chain_type_id"] is None
    assert created["issued_count"] == 0
    assert created["remaining_supply"] == created["total_supply"] == 500

    fetched = client.get(f"{URL}/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


def test_idempotency_key_is_required(client: TestClient) -> None:
    assert client.post(URL, json=body()).status_code == 422


def test_duplicate_name_is_rejected(client: TestClient) -> None:
    client.post(URL, json=body(), headers={"Idempotency-Key": "key-1"})
    response = client.post(
        URL, json=body(name="Summer Promo"), headers={"Idempotency-Key": "key-2"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_coupon_type_name"


def test_invalid_date_range_is_rejected(client: TestClient) -> None:
    response = client.post(
        URL,
        json=body(start_date=1_800_000_000, expire_date=1_700_000_000),
        headers={"Idempotency-Key": "key-1"},
    )
    assert response.status_code == 422


def test_same_idempotency_key_replays_identical_response(client: TestClient) -> None:
    first = client.post(URL, json=body(), headers={"Idempotency-Key": "key-1"})
    second = client.post(URL, json=body(), headers={"Idempotency-Key": "key-1"})
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


def test_same_idempotency_key_with_different_body_conflicts(client: TestClient) -> None:
    client.post(URL, json=body(), headers={"Idempotency-Key": "key-1"})
    response = client.post(
        URL, json=body(name="Different Promo"), headers={"Idempotency-Key": "key-1"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_key_conflict"


def test_concurrent_identical_requests_resolve_to_one_coupon_type(client: TestClient) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(URL, json=body(), headers={"Idempotency-Key": "key-1"}),
                range(8),
            )
        )
    assert all(response.status_code == 201 for response in responses)
    ids = {response.json()["id"] for response in responses}
    assert len(ids) == 1
    assert client.get(URL).json()["total"] == 1


def test_get_unknown_coupon_type_is_not_found(client: TestClient) -> None:
    response = client.get(f"{URL}/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "coupon_type_not_found"


def test_list_is_paginated(client: TestClient) -> None:
    for index in range(3):
        client.post(
            URL, json=body(name=f"Promo {index}"), headers={"Idempotency-Key": f"key-{index}"}
        )
    page = client.get(URL, params={"limit": 2}).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
