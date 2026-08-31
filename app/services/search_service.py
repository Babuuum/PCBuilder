from collections.abc import Awaitable, Callable
from typing import Any

from celery import chord
from celery.result import AsyncResult

from app.parsers.dns_parsers import DnsProvider

ParserResult = list[dict[str, Any]]
ParserRunner = Callable[[str, int, int], Awaitable[ParserResult]]


async def _run_dns(part_name: str, limit: int, search_depth: int) -> ParserResult:
    parser = DnsProvider(component_counts=limit, search_depth=search_depth)
    return await parser.start_parse(part_name=part_name)


# Adding a provider requires registering one adapter here. Parser task concurrency is
# controlled by the dedicated Celery worker and must remain at five or fewer processes.
PARSER_REGISTRY: dict[str, ParserRunner] = {"dns": _run_dns}


async def run_parser(
    provider: str,
    part_name: str,
    limit: int,
    search_depth: int,
) -> ParserResult:
    try:
        runner = PARSER_REGISTRY[provider]
    except KeyError as exc:
        raise ValueError(f"Unknown parser provider: {provider}") from exc

    return await runner(part_name, limit, search_depth)


def enqueue_search(part_name: str, limit: int, search_depth: int) -> AsyncResult:
    # Imported lazily to keep the task module and provider registry independent.
    from app.tasks import aggregate_search_results, parse_provider

    parser_jobs = [
        parse_provider.s(
            provider=provider,
            part_name=part_name,
            limit=limit,
            search_depth=search_depth,
        )
        for provider in PARSER_REGISTRY
    ]
    workflow = chord(parser_jobs, aggregate_search_results.s())
    return workflow.apply_async()
