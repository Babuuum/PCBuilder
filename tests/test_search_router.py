from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.search_router import SearchJobStatus
from app.services.search_job_service import RateLimitExceeded, SearchReservation


@pytest.fixture(autouse=True)
def mock_search_job_store(monkeypatch):
    store = SimpleNamespace(
        enforce_rate_limit=AsyncMock(),
        reserve=AsyncMock(
            side_effect=lambda *_: SearchReservation(task_id=str(uuid4()), is_new=True)
        ),
        release=AsyncMock(),
        exists=AsyncMock(return_value=True),
    )
    monkeypatch.setattr("app.api.v1.search_router.search_job_store", store)
    return store


async def test_create_search_job_returns_task_id(
    client: AsyncClient,
    monkeypatch,
    mock_search_job_store,
) -> None:
    task_id = str(uuid4())
    captured_params = {}
    mock_search_job_store.reserve.side_effect = None
    mock_search_job_store.reserve.return_value = SearchReservation(
        task_id=task_id,
        is_new=True,
    )

    def enqueue_search(**params):
        captured_params.update(params)
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr("app.api.v1.search_router.enqueue_search", enqueue_search)

    response = await client.post(
        "/api/v1/search/jobs",
        json={"name": "Intel Core i5", "limit": 5, "sort": "popular"},
    )

    assert response.status_code == 202
    assert response.json() == {"task_id": task_id, "status": "pending"}
    assert captured_params == {
        "part_name": "Intel Core i5",
        "limit": 5,
        "sort": "popular",
        "region": "Санкт-Петербург",
        "task_id": task_id,
    }


async def test_get_search_job_returns_normalized_result(client: AsyncClient, monkeypatch) -> None:
    task_id = uuid4()
    product = {
        "provider": "dns",
        "name": "CPU",
        "price_text": "10 ₽",
        "price_value": 10,
        "currency": "RUB",
    }
    task = SimpleNamespace(
        status="SUCCESS",
        result={"items": [product]},
        successful=lambda: True,
        failed=lambda: False,
    )
    monkeypatch.setattr("app.api.v1.search_router.celery_app.AsyncResult", lambda _: task)

    response = await client.get(f"/api/v1/search/jobs/{task_id}")

    assert response.status_code == 200
    response_product = response.json()["result"]["items"][0]
    assert response_product["price_text"] == "10 ₽"
    assert response_product["price_value"] == 10
    assert response_product["provider"] == "dns"


@pytest.mark.parametrize("celery_status", ["PENDING", "STARTED"])
async def test_get_search_job_returns_active_status(
    client: AsyncClient,
    monkeypatch,
    celery_status: str,
) -> None:
    task_id = uuid4()
    task = SimpleNamespace(
        status=celery_status,
        successful=lambda: False,
        failed=lambda: False,
    )
    monkeypatch.setattr("app.api.v1.search_router.celery_app.AsyncResult", lambda _: task)

    response = await client.get(f"/api/v1/search/jobs/{task_id}")

    assert response.status_code == 200
    assert response.json()["status"] == celery_status.lower()


async def test_get_search_job_hides_failure_details(client: AsyncClient, monkeypatch) -> None:
    task_id = uuid4()
    task = SimpleNamespace(
        status="FAILURE",
        result=RuntimeError("secret internal details"),
        successful=lambda: False,
        failed=lambda: True,
    )
    monkeypatch.setattr("app.api.v1.search_router.celery_app.AsyncResult", lambda _: task)

    response = await client.get(f"/api/v1/search/jobs/{task_id}")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": str(task_id),
        "status": "failure",
        "result": None,
        "error": "Search task failed",
    }


async def test_get_search_job_rejects_invalid_task_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/search/jobs/not-a-uuid")

    assert response.status_code == 422


async def test_create_search_job_rejects_unknown_sort(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/search/jobs",
        json={"name": "Intel Core i5", "limit": 5, "sort": "cheap"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("name", "expected_status"),
    [
        ("a", 422),
        ("ab", 202),
        ("a" * 100, 202),
        ("a" * 101, 422),
    ],
)
async def test_create_search_job_validates_name_boundaries(
    client: AsyncClient,
    monkeypatch,
    name: str,
    expected_status: int,
) -> None:
    monkeypatch.setattr(
        "app.api.v1.search_router.enqueue_search",
        lambda **_: SimpleNamespace(id=str(uuid4())),
    )

    response = await client.post(
        "/api/v1/search/jobs",
        json={"name": name, "limit": 20, "sort": "popular"},
    )

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    ("limit", "expected_status"),
    [(None, 202), (0, 422), (1, 202), (100, 202), (101, 422)],
)
async def test_create_search_job_validates_limit_boundaries(
    client: AsyncClient,
    monkeypatch,
    limit: int | None,
    expected_status: int,
) -> None:
    monkeypatch.setattr(
        "app.api.v1.search_router.enqueue_search",
        lambda **_: SimpleNamespace(id=str(uuid4())),
    )

    response = await client.post(
        "/api/v1/search/jobs",
        json={"name": "CPU", "limit": limit, "sort": "popular"},
    )

    assert response.status_code == expected_status


async def test_unknown_or_expired_task_returns_404(
    client: AsyncClient,
    monkeypatch,
    mock_search_job_store,
) -> None:
    task_id = uuid4()
    mock_search_job_store.exists.return_value = False

    response = await client.get(f"/api/v1/search/jobs/{task_id}")

    assert response.status_code == 404


async def test_redis_outage_returns_503(mock_search_job_store) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    mock_search_job_store.enforce_rate_limit.side_effect = RedisConnectionError("redis unavailable")
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/search/jobs",
            json={"name": "CPU", "limit": 20, "sort": "popular"},
        )

    assert response.status_code == 503


async def test_repeated_posts_reuse_active_task(
    client: AsyncClient,
    monkeypatch,
    mock_search_job_store,
) -> None:
    task_id = str(uuid4())
    mock_search_job_store.reserve.side_effect = [
        SearchReservation(task_id=task_id, is_new=True),
        SearchReservation(task_id=task_id, is_new=False),
    ]
    enqueue_search = Mock()
    monkeypatch.setattr(
        "app.api.v1.search_router.enqueue_search",
        enqueue_search,
    )
    payload = {"name": "CPU", "limit": 20, "sort": "popular"}

    first = await client.post("/api/v1/search/jobs", json=payload)
    second = await client.post("/api/v1/search/jobs", json=payload)

    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"] == task_id
    enqueue_search.assert_called_once()


async def test_rate_limit_returns_429(client: AsyncClient, mock_search_job_store) -> None:
    mock_search_job_store.enforce_rate_limit.side_effect = RateLimitExceeded

    response = await client.post(
        "/api/v1/search/jobs",
        json={"name": "CPU", "limit": 20, "sort": "popular"},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


async def test_broker_outage_releases_reservation(
    client: AsyncClient,
    monkeypatch,
    mock_search_job_store,
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    task_id = str(uuid4())
    mock_search_job_store.reserve.side_effect = None
    mock_search_job_store.reserve.return_value = SearchReservation(task_id=task_id, is_new=True)

    def unavailable_broker(**_):
        raise RedisConnectionError("broker unavailable")

    monkeypatch.setattr("app.api.v1.search_router.enqueue_search", unavailable_broker)

    response = await client.post(
        "/api/v1/search/jobs",
        json={"name": "CPU", "limit": 20, "sort": "popular"},
    )

    assert response.status_code == 503
    mock_search_job_store.release.assert_awaited_once_with(
        "CPU",
        20,
        "popular",
        "Санкт-Петербург",
        task_id,
        "127.0.0.1",
    )


async def test_custom_region_is_passed_to_reservation_and_task(
    client: AsyncClient,
    monkeypatch,
    mock_search_job_store,
) -> None:
    enqueue_search = Mock()
    monkeypatch.setattr("app.api.v1.search_router.enqueue_search", enqueue_search)

    response = await client.post(
        "/api/v1/search/jobs",
        json={"name": "CPU", "limit": None, "sort": "popular", "region": " Москва "},
    )

    assert response.status_code == 202
    mock_search_job_store.reserve.assert_awaited_once_with(
        "CPU", None, "popular", "Москва", "127.0.0.1"
    )
    assert enqueue_search.call_args.kwargs["region"] == "Москва"
    assert enqueue_search.call_args.kwargs["limit"] is None


def test_success_result_with_all_dns_fields_is_json_serializable() -> None:
    response = SearchJobStatus(
        task_id=str(uuid4()),
        status="success",
        result={
            "items": [
                {
                    "provider": "dns",
                    "name": "CPU",
                    "price_text": "13 499 ₽",
                    "price_value": 13499,
                    "currency": "RUB",
                    "stock": "in stock",
                    "url": "https://www.dns-shop.ru/product/cpu/",
                    "rating": 4.93,
                    "rating_type": "product",
                    "product_code": "5054483",
                    "product_id": "product-id",
                    "availability_status": "now",
                }
            ]
        },
    )

    serialized = response.model_dump_json()

    assert '"price_value":13499' in serialized
    assert '"availability_status":"now"' in serialized
