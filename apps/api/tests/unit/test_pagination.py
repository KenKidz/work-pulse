"""Unit tests for pagination clamping (pinned contract: clamped, never an error)."""

from app.shared.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, clamp_page_params


def test_defaults() -> None:
    params = clamp_page_params(page=1, page_size=DEFAULT_PAGE_SIZE)

    assert params.page == 1
    assert params.page_size == DEFAULT_PAGE_SIZE
    assert params.offset == 0
    assert params.limit == DEFAULT_PAGE_SIZE


def test_page_zero_clamps_to_one() -> None:
    params = clamp_page_params(page=0, page_size=20)

    assert params.page == 1
    assert params.offset == 0


def test_huge_page_size_clamps_to_max() -> None:
    params = clamp_page_params(page=2, page_size=10_000)

    assert params.page_size == MAX_PAGE_SIZE
    assert params.offset == MAX_PAGE_SIZE


def test_zero_and_negative_page_size_clamp_to_one() -> None:
    assert clamp_page_params(page=1, page_size=0).page_size == 1
    assert clamp_page_params(page=1, page_size=-5).page_size == 1


def test_make_page_computes_total_pages() -> None:
    params = clamp_page_params(page=1, page_size=20)

    page = params.make_page(total_items=25)

    assert page.model_dump() == {
        "page": 1,
        "page_size": 20,
        "total_items": 25,
        "total_pages": 2,
    }


def test_make_page_empty_collection() -> None:
    page = clamp_page_params(page=1, page_size=20).make_page(total_items=0)

    assert page.total_items == 0
    assert page.total_pages == 0
