from uuid import UUID

from celery.result import AsyncResult
from fastapi import APIRouter, status

from app.celery_app import celery_app
from app.schemas.search_router import SearchJobCreated, SearchJobStatus, SearchParams
from app.services.search_service import enqueue_search

router = APIRouter(prefix="/search", tags=["search"])


@router.post("/jobs", response_model=SearchJobCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_search_job(params: SearchParams) -> SearchJobCreated:
    task = enqueue_search(
        part_name=params.name,
        limit=params.limit,
        search_depth=params.search_depth,
    )
    return SearchJobCreated(task_id=task.id)


@router.get("/jobs/{task_id}", response_model=SearchJobStatus)
async def get_search_job(task_id: UUID) -> SearchJobStatus:
    task: AsyncResult = celery_app.AsyncResult(str(task_id))
    normalized_status = task.status.lower()

    if task.successful():
        return SearchJobStatus(
            task_id=str(task_id),
            status="success",
            result=task.result,
        )
    if task.failed():
        return SearchJobStatus(
            task_id=str(task_id),
            status="failure",
            error="Search task failed",
        )
    if normalized_status not in {"pending", "started", "retry", "revoked"}:
        normalized_status = "pending"

    return SearchJobStatus(task_id=str(task_id), status=normalized_status)
