from unittest.mock import AsyncMock

from app.celery_app import celery_app
from app.tasks import aggregate_search_results, parse_provider, ping


def test_celery_uses_redis() -> None:
    assert celery_app.conf.broker_url.startswith("redis://")
    assert celery_app.conf.result_backend.startswith("redis://")
    assert celery_app.conf.worker_concurrency == 5


def test_ping_task_returns_pong() -> None:
    assert ping.run() == "pong"


def test_parse_provider_runs_async_parser(monkeypatch) -> None:
    run_parser = AsyncMock(return_value=[{"name": "raw dns item"}])
    monkeypatch.setattr("app.tasks.run_parser", run_parser)

    result = parse_provider.run(
        provider="dns",
        part_name="processor",
        limit=5,
        search_depth=3,
    )

    assert result == {"provider": "dns", "items": [{"name": "raw dns item"}]}
    run_parser.assert_awaited_once_with("dns", "processor", 5, 3)


def test_aggregate_search_results_preserves_raw_provider_data() -> None:
    raw_result = [{"provider": "dns", "items": [{"price": "10 000 ₽"}]}]

    assert aggregate_search_results.run(raw_result) == {"providers": raw_result}
