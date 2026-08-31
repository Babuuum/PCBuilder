from types import SimpleNamespace
from uuid import uuid4

from httpx import AsyncClient


async def test_create_search_job_returns_task_id(client: AsyncClient, monkeypatch) -> None:
    task_id = str(uuid4())

    def enqueue_search(**_):
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr("app.api.v1.search_router.enqueue_search", enqueue_search)

    response = await client.post(
        "/api/v1/search/jobs",
        json={"name": "Intel Core i5", "limit": 5, "search_depth": 3},
    )

    assert response.status_code == 202
    assert response.json() == {"task_id": task_id, "status": "pending"}


async def test_get_search_job_returns_raw_result(client: AsyncClient, monkeypatch) -> None:
    task_id = uuid4()
    task = SimpleNamespace(
        status="SUCCESS",
        result={"providers": [{"provider": "dns", "items": [{"price": "10 ₽"}]}]},
        successful=lambda: True,
        failed=lambda: False,
    )
    monkeypatch.setattr("app.api.v1.search_router.celery_app.AsyncResult", lambda _: task)

    response = await client.get(f"/api/v1/search/jobs/{task_id}")

    assert response.status_code == 200
    assert response.json()["result"] == task.result


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
