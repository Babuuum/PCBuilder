from uuid import UUID

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException, Request, status
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError

from app.celery_app import celery_app
from app.core.config import get_settings
from app.schemas.search_router import SearchJobCreated, SearchJobStatus, SearchParams
from app.services.search_job_service import RateLimitExceeded, SearchJobStore
from app.services.search_service import enqueue_search

router = APIRouter(prefix="/search", tags=["search"])
settings = get_settings()
search_job_store = SearchJobStore(
    redis_url=settings.redis_url,
    ttl_seconds=3600,
    rate_limit=settings.search_rate_limit,
    rate_window_seconds=settings.search_rate_window_seconds,
)


@router.post("/jobs", response_model=SearchJobCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_search_job(params: SearchParams, request: Request) -> SearchJobCreated:
    client_key = request.client.host if request.client else "unknown"
    try:
        await search_job_store.enforce_rate_limit(client_key)
        reservation = await search_job_store.reserve(
            params.name,
            params.limit,
            params.sort.value,
            params.region,
            client_key,
        )
        if reservation.is_new:
            try:
                enqueue_search(
                    part_name=params.name,
                    limit=params.limit,
                    sort=params.sort.value,
                    region=params.region,
                    task_id=reservation.task_id,
                )
            except (RedisError, OperationalError):
                await search_job_store.release(
                    params.name,
                    params.limit,
                    params.sort.value,
                    params.region,
                    reservation.task_id,
                    client_key,
                )
                raise
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Search rate limit exceeded",
            headers={"Retry-After": str(settings.search_rate_window_seconds)},
        ) from exc
    except (RedisError, OperationalError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search queue is unavailable",
        ) from exc

    return SearchJobCreated(task_id=reservation.task_id)


@router.get("/jobs/{task_id}", response_model=SearchJobStatus)
async def get_search_job(task_id: UUID, request: Request) -> SearchJobStatus:
    task_id_value = str(task_id)
    client_key = request.client.host if request.client else "unknown"
    try:
        if not await search_job_store.exists(task_id_value, client_key):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Search task not found",
            )
        task: AsyncResult = celery_app.AsyncResult(task_id_value)
        normalized_status = task.status.lower()
    except (RedisError, OperationalError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search queue is unavailable",
        ) from exc

    if task.successful():
        return SearchJobStatus(
            task_id=task_id_value,
            status="success",
            result=task.result,
        )
    if task.failed():
        return SearchJobStatus(
            task_id=task_id_value,
            status="failure",
            error="Search task failed",
        )
    if normalized_status not in {"pending", "started", "retry", "revoked"}:
        normalized_status = "pending"

    return SearchJobStatus(task_id=task_id_value, status=normalized_status)
