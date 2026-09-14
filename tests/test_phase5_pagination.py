#!/usr/bin/env python3
"""Coverage for Scripts/phase5.py's page-break math. This is pure height
arithmetic with no ReportLab canvas involved, so unlike the rest of Phase 5
(rendered PDF output, verified manually per AGENTS.md) it's straightforward
to unit-test directly."""

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "Scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from phase5 import (  # noqa: E402
    draw_header_page,
    draw_word_bank,
    measure_block_height,
    paginate_blocks,
    paginate_blocks_at_least_one_page,
)


def _blocks(n, lines_per_block=1):
    """n blocks, each with lines_per_block placeholder lines."""
    return [["x"] * lines_per_block for _ in range(n)]


def test_measure_block_height_counts_lines_and_gaps():
    wrapped = [["one line"], ["two", "lines"], ["a", "b", "c"]]
    # 1 + 2 + 3 = 6 lines, 3 items
    assert measure_block_height(wrapped, 0, 3, gap=10, line_height=5) == 6 * 5 + 3 * 10


def test_measure_block_height_partial_range():
    wrapped = [["one"], ["two", "lines"], ["a", "b", "c"]]
    assert measure_block_height(wrapped, 1, 3, gap=10, line_height=5) == 5 * 5 + 2 * 10


def test_paginate_blocks_everything_fits_on_one_page():
    wrapped = _blocks(5)
    pages = paginate_blocks(wrapped, first_page_available_h=10_000, cont_page_available_h=10_000)
    assert pages == [(0, 5)]


def test_paginate_blocks_splits_across_pages():
    # Each block needs (line_height + gap) = 1 + 1 = 2 units of height.
    # first page fits 3 blocks (6), continuation pages fit 2 blocks (4).
    wrapped = _blocks(7)
    pages = paginate_blocks(
        wrapped, first_page_available_h=6, cont_page_available_h=4, gap=1, line_height=1
    )
    assert pages == [(0, 3), (3, 5), (5, 7)]


def test_paginate_blocks_no_trailing_empty_page():
    # Everything fits on the first page - there must be no second (empty) page.
    wrapped = _blocks(3)
    pages = paginate_blocks(wrapped, first_page_available_h=100, cont_page_available_h=100, gap=1, line_height=1)
    assert len(pages) == 1


def test_paginate_blocks_empty_input():
    assert paginate_blocks([], first_page_available_h=100, cont_page_available_h=100) == []


def test_paginate_blocks_oversized_block_gets_its_own_page_without_looping():
    # A block that alone exceeds the available height must still be placed
    # (and the loop must terminate) rather than being dropped or hanging.
    wrapped = [["short"], ["way", "too", "many", "lines", "to", "ever", "fit"], ["short"]]
    pages = paginate_blocks(wrapped, first_page_available_h=3, cont_page_available_h=3, gap=0, line_height=1)
    assert pages == [(0, 1), (1, 2), (2, 3)]
    # every index is covered exactly once
    covered = [i for start, end in pages for i in range(start, end)]
    assert covered == [0, 1, 2]


def test_paginate_blocks_reusable_for_fixed_height_rows():
    # The answer key treats every entry as a single fixed-height row with no
    # inter-row gap - same helper, different gap/line_height.
    wrapped = _blocks(9)
    pages = paginate_blocks(wrapped, first_page_available_h=20, cont_page_available_h=20, gap=0, line_height=6)
    # 20 // 6 = 3 rows per page
    assert pages == [(0, 3), (3, 6), (6, 9)]


def test_paginate_blocks_at_least_one_page_passes_through_nonempty_input():
    wrapped = _blocks(4)
    assert paginate_blocks_at_least_one_page(wrapped, 100, 100) == paginate_blocks(wrapped, 100, 100)


def test_paginate_blocks_at_least_one_page_guarantees_a_page_for_empty_input():
    # build_section must always emit a question page and an answer-key page,
    # even for a section with zero vocabulary entries - never a 0-page PDF.
    assert paginate_blocks_at_least_one_page([], 100, 100) == [(0, 0)]


def test_draw_header_page_measurement_mode_matches_drawing_mode_math():
    # Passing c=None must compute the same resulting y as a real draw would,
    # since build_section relies on the measurement pass to size pages before
    # any drawing happens.
    kwargs = dict(header_format="{theme}", subtitle="A subtitle", page=1, total_pages=3, show_instructions=True)
    measured_y = draw_header_page(None, **kwargs)
    assert isinstance(measured_y, (int, float))
    # Turning off instructions must yield a larger (higher) y - less content drawn.
    y_without_instructions = draw_header_page(None, **{**kwargs, "show_instructions": False})
    assert y_without_instructions > measured_y


def test_draw_header_page_measurement_mode_ignores_missing_subtitle():
    with_subtitle = draw_header_page(
        None, header_format="{theme}", subtitle="x", page=1, total_pages=1, show_instructions=False
    )
    without_subtitle = draw_header_page(
        None, header_format="{theme}", subtitle="", page=1, total_pages=1, show_instructions=False
    )
    assert without_subtitle > with_subtitle


def test_draw_word_bank_measurement_mode_grows_with_more_rows():
    small_bank = [("cat", 1), ("dog", 1)]
    large_bank = [("cat", 1), ("dog", 1), ("bird", 2), ("fish", 3), ("mouse", 1)]
    y_start = 500
    y_after_small = draw_word_bank(None, small_bank, y_start)
    y_after_large = draw_word_bank(None, large_bank, y_start)
    # More rows (here, more distinct words split across the two columns) means
    # a taller box, so the resulting y must be lower (less space remains).
    assert y_after_large < y_after_small
