#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Generate a PDF worksheet from a JSON file of vocabulary questions.

INPUT JSON (list of entries). Each entry must have:
  - "word": the missing word itself, in the form used (e.g., "rigged", "credit")
  - "definition": the definition of the word as used
  - "part_of_speech": the part of speech as used (e.g., "noun", "verb")
  - "sentence": the full sentence WITH '###' where the blank should appear

Example entry:
{
  "word": "credit",
  "definition": "praise or recognition for something done",
  "part_of_speech": "noun",
  "sentence": "Be sure to give Hana ### for rewriting the chorus."
}

OUTPUT:
  - A PDF with:
      Page 1: Header, instructions, Word Bank (counts), questions (auto-numbered)
      Page 2+: Continuation of questions, as many pages as needed to fit them all
      Then: ANSWER KEY (answers 1..N), spanning as many pages as needed

By default the look/feel matches your most recent Section 6 worksheet:
  Header: "Avery's WordlyWise - Section 6"
  Subtitle: "Gusts over a chasm, we rig the schedule."
  No Name/Date lines; ASCII-safe punctuation.
"""

# TESTABILITY: Phase 5 renders PDFs via ReportLab; the drawing calls produce
# binary/visual output that is validated manually (see AGENTS.md's Definition
# of Done), not unit-tested. The page-break math is pure and is unit-tested
# in tests/test_phase5_pagination.py.

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import io

# ReportLab
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.lib.utils import ImageReader

# -----------------------------
# Layout and style constants
# -----------------------------
PAGE_W, PAGE_H = letter
M_LEFT = 0.65 * inch
M_RIGHT = 0.65 * inch
M_TOP = 0.80 * inch
M_BOTTOM = 0.65 * inch

TITLE_FONT = "Helvetica-Bold"
TITLE_SIZE = 24
SUBTITLE_FONT = "Helvetica-Oblique"
SUBTITLE_SIZE = 13
TEXT_FONT = "Helvetica"
TEXT_SIZE = 12
LABEL_FONT = "Helvetica-Bold"
LABEL_SIZE = 13
WB_FONT = "Helvetica"
WB_SIZE = 12

LINE_HEIGHT = TEXT_SIZE + 6
BASE_GAP_BETWEEN_PROBLEMS = 24      # vertical gap between questions
EXTRA_SPACE_BEFORE_FIRST_Q = 60     # white space for writing before first Q on each page

CONTENT_W = PAGE_W - M_LEFT - M_RIGHT
BLANK = "______"  # what we draw for the missing word in sentences

INSTRUCTIONS = (
    "Fill in each blank with the correct word. Use each word as many times as shown in the Word Bank. "
    "Don't forget to review your answers."
)

# -----------------------------
# Utilities
# -----------------------------
def wrap_text(text, font_name, font_size, max_width):
    """Simple word wrap based on stringWidth."""
    words = text.split(" ")
    lines, line = [], ""
    for w in words:
        trial = (line + " " + w).strip()
        if stringWidth(trial, font_name, font_size) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines

def normalize_ascii(s):
    """Ensure ASCII-safe punctuation (replace smart quotes/emdashes if present)."""
    repl = {
        "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "-", "\u2026": "...", "–": "-", "—": "-",
        "’": "'", "“": '"', "”": '"',
        "•": "-", "·": "-",
    }
    for k,v in repl.items():
        s = s.replace(k, v)
    return s

def sentence_with_blank(s):
    """Replace '###' with visible blank."""
    return s.replace("###", BLANK)

def guess_base_form(word):
    """
    Very small heuristic to group counts for the Word Bank.
    Lowercases; if endswith 'ed' and leaves a doubled consonant, reduce it:
      'rigged' -> 'rig'
    Otherwise returns the lowercased word.
    """
    w = word.strip().lower()
    if w.endswith("ed") and len(w) >= 4:
        base = w[:-2]  # remove 'ed'
        # reduce double consonant if present (rigg -> rig, stopped -> stopp -> stop)
        if len(base) >= 2 and base[-1] == base[-2]:
            base = base[:-1]
        return base
    return w

def compute_word_counts(entries):
    """
    Build counts for the Word Bank using guessed base forms.
    Returns: list of tuples [(display_word, count), ...] sorted by display_word.
    """
    # Count by base
    base_counts = Counter()
    # Map base -> set of displayed forms (to choose a nice display label)
    forms_for_base = defaultdict(Counter)

    for e in entries:
        form = e["word"]
        base = guess_base_form(form)
        base_counts[base] += 1
        forms_for_base[base][form] += 1

    # Choose the most common displayed form as the label for each base
    labeled = []
    for base, cnt in base_counts.items():
        common_form = forms_for_base[base].most_common(1)[0][0]
        labeled.append((common_form, cnt))

    # Sort alphabetically by the label shown
    labeled.sort(key=lambda t: t[0].lower())
    return labeled

def measure_block_height(
    wrapped_list: List[List[str]],
    start_idx: int,
    end_idx: int,
    gap: float = BASE_GAP_BETWEEN_PROBLEMS,
    line_height: float = LINE_HEIGHT,
) -> float:
    """Compute the minimum height needed to draw lines from start_idx..end_idx with gaps."""
    num_lines = sum(len(wrapped_list[i]) for i in range(start_idx, end_idx))
    num_items = (end_idx - start_idx)
    total_min_height = num_lines * line_height + num_items * gap
    return total_min_height

def paginate_blocks(
    wrapped_list: List[List[str]],
    first_page_available_h: float,
    cont_page_available_h: float,
    gap: float = BASE_GAP_BETWEEN_PROBLEMS,
    line_height: float = LINE_HEIGHT,
) -> List[Tuple[int, int]]:
    """
    Split wrapped_list into as many pages as needed, each fitting within
    first_page_available_h (for the first page) or cont_page_available_h
    (for every page after it). Pure height arithmetic - no ReportLab canvas
    involved - so page counts can be determined before any drawing happens.

    Returns a list of (start_idx, end_idx) index ranges, one per page,
    covering the whole list.

    A single block that doesn't fit even alone on an empty page is placed on
    its own page anyway (it will render past the bottom margin) rather than
    looping forever; in practice content is short enough that this shouldn't
    happen.
    """
    pages = []
    idx = 0
    n = len(wrapped_list)
    while idx < n:
        available_h = first_page_available_h if not pages else cont_page_available_h
        end = idx
        for i in range(idx + 1, n + 1):
            if measure_block_height(wrapped_list, idx, i, gap=gap, line_height=line_height) <= available_h:
                end = i
            else:
                break
        if end == idx:
            end = idx + 1
        pages.append((idx, end))
        idx = end
    return pages

def paginate_blocks_at_least_one_page(
    wrapped_list: List[List[str]],
    first_page_available_h: float,
    cont_page_available_h: float,
    gap: float = BASE_GAP_BETWEEN_PROBLEMS,
    line_height: float = LINE_HEIGHT,
) -> List[Tuple[int, int]]:
    """
    Same as paginate_blocks, but guarantees at least one (possibly empty)
    page instead of zero - build_section must always emit a question page
    and an answer-key page even when there's no content, rather than a
    0-page PDF.
    """
    return paginate_blocks(
        wrapped_list, first_page_available_h, cont_page_available_h, gap=gap, line_height=line_height
    ) or [(0, 0)]

def draw_header_page(
    c: Optional[canvas.Canvas],
    header_format: str,
    subtitle: str,
    page: int,
    total_pages: int,
    show_instructions: bool,
    title_suffix: str = "",
) -> float:
    """
    Compute (and, if c is given, draw) the centered header + subtitle block;
    returns next y. Pass c=None to measure the resulting y without drawing -
    used to size pages before pagination is known.
    """
    header = build_presentation_str(header_format, page, total_pages)
    if title_suffix:
        header = f"{header} {title_suffix}"

    y = PAGE_H - M_TOP
    if c:
        c.setFont(TITLE_FONT, TITLE_SIZE)
        c.drawString((PAGE_W - stringWidth(header, TITLE_FONT, TITLE_SIZE)) / 2, y, header)
    y -= (TITLE_SIZE + 6)

    if subtitle:
        if c:
            c.setFont(SUBTITLE_FONT, SUBTITLE_SIZE)
            c.drawString((PAGE_W - stringWidth(subtitle, SUBTITLE_FONT, SUBTITLE_SIZE)) / 2, y, subtitle)
        y -= (SUBTITLE_SIZE + 16)

    if show_instructions:
        if c:
            c.setFont(TEXT_FONT, TEXT_SIZE)
        for ln in wrap_text(INSTRUCTIONS, TEXT_FONT, TEXT_SIZE, CONTENT_W):
            if c:
                c.drawString(M_LEFT, y, ln)
            y -= LINE_HEIGHT
        y -= 8
    return y

def draw_word_bank(c: Optional[canvas.Canvas], word_counts: List[Tuple[str, int]], y_start: float) -> float:
    """
    Compute (and, if c is given, draw) the 'Word Bank (number of uses):'
    label + full-width box with two columns that expand to content.
    Returns next y (with extra space before questions). Pass c=None to
    measure the resulting y without drawing.
    """
    y = y_start
    if c:
        c.setFont(LABEL_FONT, LABEL_SIZE)
        c.drawString(M_LEFT, y, "Word Bank (number of uses):")
    y -= (LABEL_SIZE + 8)

    padding_lr = 10
    padding_tb = 10
    gap = 24

    # split into two roughly even columns
    split_idx = (len(word_counts) + 1) // 2
    left_items = word_counts[:split_idx]
    right_items = word_counts[split_idx:]

    box_w = CONTENT_W
    box_x = M_LEFT
    inner_w = box_w - 2 * padding_lr

    def label_text(item):
        w, cnt = item
        return f"{w} ({cnt})"

    left_max_w = max((stringWidth(label_text(it), WB_FONT, WB_SIZE) for it in left_items), default=0)
    right_max_w = max((stringWidth(label_text(it), WB_FONT, WB_SIZE) for it in right_items), default=0)

    requested_w = left_max_w + gap + right_max_w
    if requested_w <= inner_w:
        col1_x = box_x + padding_lr
        col2_x = box_x + padding_lr + left_max_w + gap
    else:
        # fallback to even split
        col1_x = box_x + padding_lr
        col2_x = box_x + padding_lr + (inner_w - gap) / 2 + gap

    rows = max(len(left_items), len(right_items))
    row_h = WB_SIZE + 6
    box_h = padding_tb + rows * row_h + padding_tb

    box_y_top = y
    box_y_bottom = y - box_h
    if c:
        c.rect(box_x, box_y_bottom, box_w, box_h, stroke=1, fill=0)
        c.setFont(WB_FONT, WB_SIZE)
        yy = box_y_top - padding_tb - WB_SIZE
        for i in range(rows):
            if i < len(left_items):
                c.drawString(col1_x, yy - i * row_h, label_text(left_items[i]))
            if i < len(right_items):
                c.drawString(col2_x, yy - i * row_h, label_text(right_items[i]))

    y = box_y_bottom - EXTRA_SPACE_BEFORE_FIRST_Q
    return y

def draw_questions(c, wrapped_questions, start_idx, end_idx, y_start, start_num=1):
    """
    Draws questions from start_idx..end_idx (exclusive), with auto-numbering.
    Returns (next_y, next_question_number)
    """
    y = y_start
    c.setFont(TEXT_FONT, TEXT_SIZE)
    qnum = start_num
    for i in range(start_idx, end_idx):
        lines = wrapped_questions[i]
        if not lines:
            c.drawString(M_LEFT, y, f"{qnum})")
            y -= LINE_HEIGHT
        else:
            # put number in front of first line only
            first = f"{qnum}) {lines[0]}"
            c.drawString(M_LEFT, y, first)
            y -= LINE_HEIGHT
            for ln in lines[1:]:
                c.drawString(M_LEFT, y, ln)
                y -= LINE_HEIGHT
        qnum += 1
        y -= BASE_GAP_BETWEEN_PROBLEMS
    return y, qnum

def draw_questions_footer(c, footer_text):
    """Draw a footer at the bottom of the page based on footer_text."""
    if not footer_text:
        return
    
    c.setFont(TEXT_FONT, TEXT_SIZE - 2)

    # center the footer text on the page
    center_x = (PAGE_W - stringWidth(footer_text, TEXT_FONT, TEXT_SIZE - 2)) / 2
    c.drawString(center_x, M_BOTTOM / 2, footer_text)

def draw_answer_key_page_number(c: canvas.Canvas, page: int, total_pages: int) -> None:
    """
    Draw a simple 'Answer Key Page X of Y' indicator in the bottom-right
    corner of an answer-key page, one line above the main footer row.
    Drawn on every answer-key page (unlike draw_answers_footer's footer
    text/QR, which only belongs on the last one) so a multi-page answer key
    doesn't leave earlier pages with no page indicator at all. It sits on
    its own row - not just its own corner - because draw_answers_footer's
    footer text is caller-configurable and can be long enough to reach the
    right edge, so horizontal separation alone isn't reliable.
    """
    text = f"Answer Key Page {page} of {total_pages}"
    c.setFont(TEXT_FONT, TEXT_SIZE - 2)
    text_w = stringWidth(text, TEXT_FONT, TEXT_SIZE - 2)
    y = M_BOTTOM / 2 + (TEXT_SIZE - 2) + 4
    c.drawString(PAGE_W - M_RIGHT - text_w, y, text)

def draw_answers_footer(c, footer_text, seed, qr_code):
    """Draw a footer at the bottom of the page based on footer_text."""
    if not footer_text:
        return
    
    c.setFont(TEXT_FONT, TEXT_SIZE - 2)

    if qr_code:
        try:
            b = qr_code.getBounds()
            qr_w = (b[2] - b[0]) if b else 40
            qr_h = (b[3] - b[1]) if b else 40
        except Exception:
            qr_w = qr_h = 40

        qr_x = M_LEFT
        qr_y = M_BOTTOM / 2 - (qr_h / 2) + 50
        try:
            renderPDF.draw(qr_code, c, qr_x, qr_y)
        except Exception:
            pass

        # draw "Get Episode X" text centered below the QR
        next_seed = str(seed + 1)
        if seed is not None:
            episode_text = f"Get Episode {next_seed}"
            c.setFont(TEXT_FONT, TEXT_SIZE - 2)
            ep_w = stringWidth(episode_text, TEXT_FONT, TEXT_SIZE - 2)
            ep_x = qr_x + max(0, (qr_w - ep_w) / 2)
            ep_y = qr_y - (TEXT_SIZE - 2) - 4
            c.drawString(ep_x, ep_y, episode_text)

    # center the footer text on the page
    center_x = (PAGE_W - stringWidth(footer_text, TEXT_FONT, TEXT_SIZE - 2)) / 2
    c.drawString(center_x, M_BOTTOM / 2, footer_text)

def build_presentation_str(template, page, total_pages):
    """Interpolate page/total_pages into the template string."""
    if not template:
        return ""
    formatted = template.replace("{current_page}", str(page)).replace("{total_pages}", str(total_pages))
    return formatted

def _rng_seed_from_worksheet_id(worksheet_id):
    if worksheet_id is None:
        return None
    if isinstance(worksheet_id, (int, float)):
        return worksheet_id
    # Stable, deterministic seed for non-numeric ids
    digest = hashlib.sha256(str(worksheet_id).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def build_section(
    c: canvas.Canvas,
    header_format: str,
    seed: int,
    worksheet_id: Optional[Any],
    section: Dict[str, Any],
    footer_format: str,
    answer_key_footer_format: str,
    qr_code: Optional[Drawing],
) -> None:
    
    output_subtitle = section["output"]["subtitle"]
    entries = section["data"]

    subtitle_with_episode = f"Episode {seed}: " + output_subtitle
    
    rng_seed = _rng_seed_from_worksheet_id(worksheet_id)
    if rng_seed is None:
        rng_seed = seed
    rng = random.Random(rng_seed)
    shuffled_entries = rng.sample(entries, k=len(entries)) 
    
    questions = []
    for e in shuffled_entries:
        word = normalize_ascii(e["word"])
        definition = normalize_ascii(e["definition"])
        pos = normalize_ascii(e["part_of_speech"])
        sentence = normalize_ascii(e["output"]["sentence"])
        sentence = sentence_with_blank(sentence)
        questions.append({
            "word": word, "definition": definition, "pos": pos, "sentence": sentence
        })

    # Word bank counts (try to group close forms like "rigged" -> "rig")
    word_counts = compute_word_counts(questions)

    # Wrap all questions
    wrapped = [wrap_text(q["sentence"], TEXT_FONT, TEXT_SIZE, CONTENT_W) for q in questions]

    # ---------------- Paginate questions (pure measurement, no drawing) ----------------
    first_q_y = draw_header_page(None, header_format, subtitle_with_episode, 1, 1, show_instructions=True)
    first_q_y = draw_word_bank(None, word_counts, first_q_y)
    first_q_available_h = first_q_y - M_BOTTOM

    cont_q_y = draw_header_page(None, header_format, subtitle_with_episode, 1, 1, show_instructions=False)
    cont_q_y -= EXTRA_SPACE_BEFORE_FIRST_Q
    cont_q_available_h = cont_q_y - M_BOTTOM

    question_pages = paginate_blocks_at_least_one_page(wrapped, first_q_available_h, cont_q_available_h)
    total_question_pages = len(question_pages)

    # ---------------- Draw question pages ----------------
    qnum = 1
    for page_num, (start_idx, end_idx) in enumerate(question_pages, start=1):
        is_first = (page_num == 1)
        y = draw_header_page(c, header_format, subtitle_with_episode, page_num, total_question_pages,
                              show_instructions=is_first)
        if is_first:
            y = draw_word_bank(c, word_counts, y)
        else:
            y -= EXTRA_SPACE_BEFORE_FIRST_Q

        _, qnum = draw_questions(c, wrapped, start_idx, end_idx, y, start_num=qnum)
        draw_questions_footer(c, build_presentation_str(footer_format, page_num, total_question_pages))
        c.showPage()

    # ---------------- Paginate the answer key (pure measurement, no drawing) ----------------
    answer_line_h = TEXT_SIZE + 6
    ak_y = draw_header_page(None, header_format, subtitle_with_episode, 1, 1, show_instructions=False,
                             title_suffix="(Answer Key)")
    ak_y -= (TEXT_SIZE + 10)  # "Answers:" label
    ak_available_h = ak_y - M_BOTTOM

    # Every answer-key page has an identical header (no word bank, no instructions),
    # so the same available height applies to the first page and every continuation.
    answer_blocks = [[""] for _ in questions]  # one line each; content is irrelevant to the height math
    answer_pages = paginate_blocks_at_least_one_page(
        answer_blocks, ak_available_h, ak_available_h, gap=0, line_height=answer_line_h
    )
    total_answer_pages = len(answer_pages)

    # Column alignment is computed once across all answers so it stays consistent across pages
    label_widths = []
    for i, q in enumerate(questions, start=1):
        w_num = stringWidth(f"{i}) ", TEXT_FONT, TEXT_SIZE)
        w_word = stringWidth(q['word'], TITLE_FONT, TEXT_SIZE)
        label_widths.append(w_num + w_word)
    max_label_w = max(label_widths) if label_widths else 0

    padding_between = 18
    x_def = M_LEFT + max_label_w + padding_between
    def_size = max(8, TEXT_SIZE - 2)

    # ---------------- Draw answer-key pages ----------------
    for page_num, (start_idx, end_idx) in enumerate(answer_pages, start=1):
        is_last = (page_num == total_answer_pages)
        y3 = draw_header_page(c, header_format, subtitle_with_episode, page_num, total_answer_pages,
                               show_instructions=False, title_suffix="(Answer Key)")

        c.setFont(TEXT_FONT, TEXT_SIZE)
        c.drawString(M_LEFT, y3, "Answers:")
        y3 -= (TEXT_SIZE + 10)

        for i in range(start_idx, end_idx):
            q = questions[i]
            num_text = f"{i + 1}) "
            c.setFont(TEXT_FONT, TEXT_SIZE)
            c.drawString(M_LEFT, y3, num_text)

            x_word = M_LEFT + stringWidth(num_text, TEXT_FONT, TEXT_SIZE)
            c.setFont(TITLE_FONT, TEXT_SIZE)  # bold
            c.drawString(x_word, y3, q['word'])

            c.setFont(SUBTITLE_FONT, def_size)
            def_text = f"  {q['definition']} ({q['pos']})"
            c.drawString(x_def, y3, def_text)

            y3 -= answer_line_h

        # A simple page-number indicator appears on every answer-key page;
        # the QR code / "get next episode" footer only belongs on the last one.
        draw_answer_key_page_number(c, page_num, total_answer_pages)
        if is_last:
            draw_answers_footer(c, build_presentation_str(answer_key_footer_format, page_num, total_answer_pages),
                                 seed, qr_code)
        c.showPage()


def build_pdf(doc_root, output_stream):
    c = canvas.Canvas(output_stream, pagesize=letter)
        
    worksheet_id = doc_root.get('worksheet_id')
    qr_worksheet_id = doc_root.get('qr_worksheet_id') or worksheet_id

    # Generate QR code image pointing to the next episode
    if qr_worksheet_id is not None:
        try:
            base_url = f"http://vocabhunters.com/worksheet?id={qr_worksheet_id}"
            qr_widget = QrCodeWidget(base_url)
            b = qr_widget.getBounds()
            w = b[2] - b[0]
            h = b[3] - b[1]
            d = Drawing(w, h)
            d.add(qr_widget)
            qr_code = d
        except Exception:
            qr_code = None
    else:
        qr_code = None

    header_format = doc_root["presentation_metadata"]["header"]
    seed = doc_root["seed"]

    footer_format = doc_root["presentation_metadata"]["footer"]
    answer_key_footer_format = doc_root["presentation_metadata"]["answer_key_footer"]
    build_section(c, header_format, seed, worksheet_id, doc_root, footer_format, answer_key_footer_format, qr_code)

    c.save()

# -----------------------------
# CLI
# -----------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate a PDF vocabulary worksheet from JSON.")
    args = parser.parse_args()

    # Read JSON from stdin
    try:
        doc_root = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON from stdin: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        pdf_bytes = run_from_json(doc_root)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    sys.stdout.buffer.write(pdf_bytes)


def run_from_json(doc_root):
    if isinstance(doc_root, str):
        try:
            doc_root = json.loads(doc_root)
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid JSON string: {e}") from e

    if not isinstance(doc_root, dict) or not doc_root:
        raise ValueError("JSON must be a non-empty dictionary.")

    buffer = io.BytesIO()
    build_pdf(doc_root, buffer)
    return buffer.getvalue()


def run_with_json(doc_root):
    return run_from_json(doc_root)

if __name__ == "__main__":
    main()
