from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import search_service


def test_only_dns_parser_is_registered() -> None:
    assert set(search_service.PARSER_REGISTRY) == {"dns"}


async def test_run_parser_uses_registered_adapter(monkeypatch) -> None:
    runner = AsyncMock(return_value=[{"name": "raw item"}])
    monkeypatch.setitem(search_service.PARSER_REGISTRY, "dns", runner)

    result = await search_service.run_parser("dns", "cpu", 10, 4)

    assert result == [{"name": "raw item"}]
    runner.assert_awaited_once_with("cpu", 10, 4)


async def test_run_parser_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unknown parser provider"):
        await search_service.run_parser("unknown", "cpu", 10, 4)


def test_enqueue_search_builds_workflow_for_registered_providers(monkeypatch) -> None:
    apply_async = Mock(return_value=SimpleNamespace(id="task-id"))
    workflow = SimpleNamespace(apply_async=apply_async)
    chord = Mock(return_value=workflow)
    monkeypatch.setattr(search_service, "chord", chord)

    result = search_service.enqueue_search("cpu", 10, 4)

    assert result.id == "task-id"
    parser_jobs, callback = chord.call_args.args
    assert len(parser_jobs) == 1
    assert parser_jobs[0].kwargs["provider"] == "dns"
    assert callback.task == "app.tasks.aggregate_search_results"
    apply_async.assert_called_once_with()
