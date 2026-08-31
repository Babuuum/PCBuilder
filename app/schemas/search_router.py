from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchParams(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=2, max_length=100)
    limit: int = Field(default=20, ge=1, le=100)
    search_depth: int = Field(default=20, ge=1, le=100)


class SearchJobCreated(BaseModel):
    task_id: str
    status: Literal["pending"] = "pending"


class ProviderRawResult(BaseModel):
    provider: str
    items: list[dict[str, Any]]


class SearchRawResult(BaseModel):
    providers: list[ProviderRawResult]


class SearchJobStatus(BaseModel):
    task_id: str
    status: Literal["pending", "started", "retry", "success", "failure", "revoked"]
    result: SearchRawResult | None = None
    error: str | None = None
