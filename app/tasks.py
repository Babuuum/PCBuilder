import asyncio
import time
from typing import Any

from app.celery_app import celery_app
from app.core.logging import get_logger
from app.services.search_service import run_parser

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.ping")
def ping() -> str:
    logger.info("Celery ping task completed")
    return "pong"


@celery_app.task(
    bind=True,
    name="app.tasks.parse_provider",
    soft_time_limit=280,
    time_limit=300,
)
def parse_provider(
    self,
    provider: str,
    part_name: str,
    limit: int = 20,
    search_depth: int = 20,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    logger.info(
        "Parser task started task_id=%s provider=%s query=%r",
        self.request.id,
        provider,
        part_name,
    )
    raw_items = asyncio.run(run_parser(provider, part_name, limit, search_depth))
    logger.info(
        "Parser task completed task_id=%s provider=%s items=%s elapsed=%.2fs",
        self.request.id,
        provider,
        len(raw_items),
        time.perf_counter() - started_at,
    )
    return {"provider": provider, "items": raw_items}


@celery_app.task(name="app.tasks.aggregate_search_results")
def aggregate_search_results(parser_results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"providers": parser_results}
