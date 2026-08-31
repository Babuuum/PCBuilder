import re
from collections.abc import Awaitable, Callable
from typing import Any

from celery import chord
from celery.result import AsyncResult

from app.core.config import get_settings
from app.parsers.base import identity_provider_from_json
from app.parsers.dns_parsers import DnsProvider

ParserResult = list[dict[str, Any]]
ParserRunner = Callable[[str, int | None, str], Awaitable[ParserResult]]
_IDENTITY_PROVIDERS: dict[str, object] = {}


async def _run_dns(part_name: str, limit: int | None, region: str) -> ParserResult:
    settings = get_settings()
    provider_key = settings.dns_browser_identities
    identity_provider = _IDENTITY_PROVIDERS.get(provider_key)
    if identity_provider is None:
        identity_provider = identity_provider_from_json(provider_key)
        _IDENTITY_PROVIDERS[provider_key] = identity_provider
    parser = DnsProvider(
        limit=limit,
        identity_provider=identity_provider,
        headless=settings.browser_headless,
    )
    raw_items = await parser.start_parse(part_name=part_name, region=region)
    return [normalize_dns_item(item) for item in raw_items]


def normalize_dns_item(item: dict[str, Any]) -> dict[str, Any]:
    price_text = item.get("price")
    price_digits = re.sub(r"\D", "", price_text) if isinstance(price_text, str) else ""
    rating_text = item.get("rating")
    try:
        rating_value = float(str(rating_text).replace(",", "."))
    except (TypeError, ValueError):
        rating_value = None

    return {
        "provider": "dns",
        "name": item.get("name"),
        "price_text": price_text,
        "price_value": int(price_digits) if price_digits else None,
        "currency": "RUB" if price_digits else None,
        "stock": item.get("stock"),
        "url": item.get("href"),
        "rating": rating_value,
        "rating_type": item.get("rating_type"),
        "product_code": item.get("product_code"),
        "product_id": item.get("product_id"),
        "availability_status": item.get("availability_status"),
    }


# Adding a provider requires registering one adapter here. Parser task concurrency is
# controlled by the dedicated Celery worker and must remain at five or fewer processes.
PARSER_REGISTRY: dict[str, ParserRunner] = {"dns": _run_dns}


async def run_parser(
    provider: str,
    part_name: str,
    limit: int | None,
    region: str,
) -> ParserResult:
    try:
        runner = PARSER_REGISTRY[provider]
    except KeyError as exc:
        raise ValueError(f"Unknown parser provider: {provider}") from exc

    return await runner(part_name, limit, region)


def enqueue_search(
    part_name: str,
    limit: int | None,
    sort: str,
    region: str,
    task_id: str,
) -> AsyncResult:
    # Imported lazily to keep the task module and provider registry independent.
    from app.tasks import aggregate_search_results, parse_provider

    parser_jobs = [
        parse_provider.s(
            provider=provider,
            part_name=part_name,
            limit=limit,
            region=region,
        )
        for provider in PARSER_REGISTRY
    ]
    workflow = chord(parser_jobs, aggregate_search_results.s(sort=sort))
    return workflow.apply_async(task_id=task_id)
