"""Uniform pagination (`.ai/conventions.md`): clamped, never an error.

`?page=` defaults to 1 (clamped ≥ 1); `?page_size=` defaults to 20, max 100
(clamped). Response envelope: `{"items": [...], "page": Page}`.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class Page(BaseModel):
    """Pagination metadata returned with every list response."""

    page: int
    page_size: int
    total_items: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class PageParams:
    """Validated pagination request parameters."""

    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size

    def make_page(self, total_items: int) -> Page:
        total_pages = (total_items + self.page_size - 1) // self.page_size
        return Page(
            page=self.page,
            page_size=self.page_size,
            total_items=total_items,
            total_pages=total_pages,
        )


def clamp_page_params(page: int, page_size: int) -> PageParams:
    """Pure clamping: page ≥ 1, 1 ≤ page_size ≤ 100."""
    return PageParams(
        page=max(page, 1),
        page_size=min(max(page_size, 1), MAX_PAGE_SIZE),
    )


def get_page_params(
    page: Annotated[int, Query()] = 1,
    page_size: Annotated[int, Query()] = DEFAULT_PAGE_SIZE,
) -> PageParams:
    """FastAPI dependency.

    Out-of-bound values are clamped, never rejected (pinned contract) — only
    non-integer input is a `validation_error`.
    """
    return clamp_page_params(page, page_size)


PageParamsDep = Annotated[PageParams, Depends(get_page_params)]
