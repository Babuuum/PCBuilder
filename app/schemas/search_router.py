from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SortType(StrEnum):
    POPULAR = "popular"


class SearchParams(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=2, max_length=100)
    limit: int | None = Field(default=20, ge=1, le=100)
    sort: SortType = SortType.POPULAR
    region: str = Field(default="Санкт-Петербург", min_length=2, max_length=100)

    @field_validator("name", "region")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("value must contain at least two non-whitespace characters")
        return normalized


class SearchJobCreated(BaseModel):
    task_id: str
    status: Literal["pending"] = "pending"


class SearchProduct(BaseModel):
    provider: str
    name: str | None = None
    price_text: str | None = None
    price_value: int | None = None
    currency: Literal["RUB"] | None = None
    stock: str | None = None
    url: str | None = None
    rating: float | None = None
    rating_type: str | None = None
    product_code: str | None = None
    product_id: str | None = None
    availability_status: str | None = None


class SearchResult(BaseModel):
    items: list[SearchProduct]


class SearchJobStatus(BaseModel):
    task_id: str
    status: Literal["pending", "started", "retry", "success", "failure", "revoked"]
    result: SearchResult | None = None
    error: str | None = None
