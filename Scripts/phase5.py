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
      Page 2: Continuation of questions (if needed)
      Page 3: ANSWER KEY (answers 1..N)

By default the look/feel matches your most recent Section 6 worksheet:
  Header: "Avery's WordlyWise - Section 6"
  Subtitle: "Gusts over a chasm, we rig the schedule."
  No Name/Date lines; ASCII-safe punctuation.
"""

import argparse
import json
import os
import random
import sys
from collections import Counter, defaultdict
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

def measure_block_height(wrapped_list, start_idx, end_idx):
    """Compute the minimum height needed to draw lines from start_idx..end_idx with gaps."""
    num_lines = sum(len(wrapped_list[i]) for i in range(start_idx, end_idx))
    num_items = (end_idx - start_idx)
    total_min_height = num_lines * LINE_HEIGHT + num_items * BASE_GAP_BETWEEN_PROBLEMS
    return total_min_height

def draw_header_page(c, header, subtitle, page, total_pages):
    """Draws the centered header + subtitle; returns next y."""
    y = PAGE_H - M_TOP
    c.setFont(TITLE_FONT, TITLE_SIZE)
    # t = header if not continued else f"{header} (page {page} of {total_pages})"
    #t = f"{header} (page {page} of {total_pages})"
    c.drawString((PAGE_W - stringWidth(header, TITLE_FONT, TITLE_SIZE)) / 2, y, header)
    y -= (TITLE_SIZE + 6)

    if subtitle:
        c.setFont(SUBTITLE_FONT, SUBTITLE_SIZE)
        c.drawString((PAGE_W - stringWidth(subtitle, SUBTITLE_FONT, SUBTITLE_SIZE)) / 2, y, subtitle)
        y -= (SUBTITLE_SIZE + 16)

    # Instructions on first page only
    if page == 1:
        c.setFont(TEXT_FONT, TEXT_SIZE)
        for ln in wrap_text(INSTRUCTIONS, TEXT_FONT, TEXT_SIZE, CONTENT_W):
            c.drawString(M_LEFT, y, ln)
            y -= LINE_HEIGHT
        y -= 8
    return y

def draw_word_bank(c, word_counts, y_start):
    """
    Draw the 'Word Bank (number of uses):' label + full-width box with two columns
    that expand to content.
    Returns next y (with extra space before questions).
    """
    y = y_start
    c.setFont(LABEL_FONT, LABEL_SIZE)
    c.drawString(M_LEFT, y, "Word Bank (number of uses):")
    y -= (LABEL_SIZE + 8)

    c.setFont(WB_FONT, WB_SIZE)
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
    c.rect(box_x, box_y_bottom, box_w, box_h, stroke=1, fill=0)

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

def draw_questions_footer(c, footer_format, presentation_metadata):
    """Draw a footer at the bottom of the page based on footer_metadata."""
    if not footer_format:
        return
    
    c.setFont(TEXT_FONT, TEXT_SIZE - 2)
    footer_text = footer_format
    for key, value in presentation_metadata.items():
        if key == "qr_code":
            continue
        placeholder = "{" + key + "}"
        footer_text = footer_text.replace(placeholder, str(value))

    # center the footer text on the page
    center_x = (PAGE_W - stringWidth(footer_text, TEXT_FONT, TEXT_SIZE - 2)) / 2
    c.drawString(center_x, M_BOTTOM / 2, footer_text)

def draw_answers_footer(c, footer_format, presentation_metadata):
    """Draw a footer at the bottom of the page based on footer_metadata."""
    if not footer_format:
        return
    
    c.setFont(TEXT_FONT, TEXT_SIZE - 2)
    footer_text = footer_format
    for key, value in presentation_metadata.items():
        if key == "qr_code":
            continue
        placeholder = "{" + key + "}"
        footer_text = footer_text.replace(placeholder, str(value))

    if presentation_metadata.get("qr_code"):
        qr = presentation_metadata["qr_code"]
        try:
            b = qr.getBounds()
            qr_w = (b[2] - b[0]) if b else 40
            qr_h = (b[3] - b[1]) if b else 40
        except Exception:
            qr_w = qr_h = 40

        qr_x = M_LEFT
        # raise the QR slightly (15 pts up from baseline)
        # qr_y = M_BOTTOM / 2 - (qr_h / 2) + 15
        qr_y = M_BOTTOM / 2 - (qr_h / 2) + 50
        try:
            renderPDF.draw(qr, c, qr_x, qr_y)
        except Exception:
            pass

        # draw "Get Episode X" text centered below the QR
        seed = int(presentation_metadata.get("seed"))
        next_seed = str(seed + 1)
        if seed is not None:
            episode_text = f"Get Episode {next_seed}"
            c.setFont(TEXT_FONT, TEXT_SIZE - 2)
            ep_w = stringWidth(episode_text, TEXT_FONT, TEXT_SIZE - 2)
            ep_x = qr_x + max(0, (qr_w - ep_w) / 2)
            ep_y = qr_y - (TEXT_SIZE - 2) - 4
            c.drawString(ep_x, M_BOTTOM / 2, episode_text)

    # center the footer text on the page
    center_x = (PAGE_W - stringWidth(footer_text, TEXT_FONT, TEXT_SIZE - 2)) / 2
    c.drawString(center_x, M_BOTTOM / 2, footer_text)

def build_section(c, section_title, seed, section, footer_format, answer_key_footer_format, presentation_variables):
    
    output_subtitle = section["output"]["subtitle"]
    entries = section["data"]

    subtitle_with_episode = f"Episode {seed}: " + output_subtitle
    
    # if not isinstance(entries, list) or not entries:
    #     print("Error: JSON must be a non-empty list of entries.", file=sys.stderr)
    #     sys.exit(1)

    # # Basic schema check
    # required = {"word", "definition", "part_of_speech", "sentence"}
    # for i, e in enumerate(entries):
    #     if not isinstance(e, dict) or not required.issubset(e.keys()):
    #         print(f"Error: entry {i} missing required keys {required}.", file=sys.stderr)
    #         sys.exit(1)
    
    rng = random.Random(seed)
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

    # ---------------- Page 1 ----------------
    y = draw_header_page(c, section_title, subtitle_with_episode, 1, 2)
    y = draw_word_bank(c, word_counts, y)
    available_h = y - M_BOTTOM

    # Find how many questions fit on page 1
    end_idx_p1 = 0
    for i in range(1, len(wrapped) + 1):
        needed = measure_block_height(wrapped, 0, i)
        if needed <= available_h:
            end_idx_p1 = i
        else:
            break

    _, next_num = draw_questions(c, wrapped, 0, end_idx_p1, y, start_num=1)
    presentation_variables["current_page"] = 1
    draw_questions_footer(c, footer_format, presentation_variables)
    c.showPage()

    # ---------------- Page 2 (questions continued) ----------------
    y2 = draw_header_page(c, section_title, subtitle_with_episode, 2, 2)
    y2 -= EXTRA_SPACE_BEFORE_FIRST_Q
    available_h2 = y2 - M_BOTTOM

    _, next_num = draw_questions(c, wrapped, end_idx_p1, len(wrapped), y2, start_num=next_num)
    presentation_variables["current_page"] = 2
    draw_questions_footer(c, footer_format, presentation_variables)
    c.showPage()

    # ---------------- Page 3 (ANSWER KEY) ----------------
    y3 = PAGE_H - M_TOP
    c.setFont(TITLE_FONT, TITLE_SIZE)
    ak_header = f"{section_title} (Answer Key)"
    c.drawString((PAGE_W - stringWidth(ak_header, TITLE_FONT, TITLE_SIZE)) / 2, y3, ak_header)
    y3 -= (TITLE_SIZE + 6)

    if subtitle_with_episode:
        c.setFont(SUBTITLE_FONT, SUBTITLE_SIZE)
        c.drawString((PAGE_W - stringWidth(subtitle_with_episode, SUBTITLE_FONT, SUBTITLE_SIZE)) / 2, y3, subtitle_with_episode)
        y3 -= (SUBTITLE_SIZE + 16)

    c.setFont(TEXT_FONT, TEXT_SIZE)
    c.drawString(M_LEFT, y3, "Answers:")
    y3 -= (TEXT_SIZE + 10)

    # Answers in the same order as questions
    # compute fixed column for definitions so they all start at the same offset
    label_widths = []
    for i, q in enumerate(questions, start=1):
        w_num = stringWidth(f"{i}) ", TEXT_FONT, TEXT_SIZE)
        w_word = stringWidth(q['word'], TITLE_FONT, TEXT_SIZE)
        label_widths.append(w_num + w_word)
    max_label_w = max(label_widths) if label_widths else 0

    # slightly wider gap and smaller font for definitions
    padding_between = 18
    x_def = M_LEFT + max_label_w + padding_between
    def_font = TEXT_FONT
    def_size = max(8, TEXT_SIZE - 2)

    for i, q in enumerate(questions, start=1):
        # number
        num_text = f"{i}) "
        c.setFont(TEXT_FONT, TEXT_SIZE)
        c.drawString(M_LEFT, y3, num_text)

        # bold word
        x_word = M_LEFT + stringWidth(num_text, TEXT_FONT, TEXT_SIZE)
        c.setFont(TITLE_FONT, TEXT_SIZE)  # bold
        c.drawString(x_word, y3, q['word'])

        # definition (smaller font) aligned to fixed x_def
        c.setFont(SUBTITLE_FONT, def_size)
        def_text = f"  {q['definition']} ({q['pos']})"
        c.drawString(x_def, y3, def_text)

        y3 -= (TEXT_SIZE + 6)
    
    draw_answers_footer(c, answer_key_footer_format, presentation_variables)
    c.showPage()

    
def build_section_title(header_format, presentation_variables):
    header_text = header_format
    for key, value in presentation_variables.items():
        placeholder = "{" + key + "}"
        header_text = header_text.replace(placeholder, str(value))
    return header_text
    
def build_pdf(doc_root, output_stream):
    c = canvas.Canvas(output_stream, pagesize=letter)
        
    worksheet_id = doc_root.get('worksheet_id')
    
    
    presentation_variables = {
        "section": doc_root["presentation_metadata"]["section"],
        "reading_system": doc_root["reading_level"]["system"],
        "reading_level": doc_root["reading_level"]["level"],
        "model": doc_root["model"],
        "seed": doc_root["seed"],
        "worksheet_id": worksheet_id,
        "total_pages": 2,
    }

    # Generate QR code image for http://cindysoftware.com/ws={worksheet_id}
    try:
        base_url = f"http://cindysoftware.com/id={worksheet_id}" if worksheet_id is not None else "http://cindysoftware.com/ws="
        qr_widget = QrCodeWidget(base_url)
        b = qr_widget.getBounds()
        w = b[2] - b[0]
        h = b[3] - b[1]
        d = Drawing(w, h)
        d.add(qr_widget)
        #png_bytes = renderPM.drawToString(d, fmt="PNG")
        #presentation_variables["qr_code"] = ImageReader(io.BytesIO(png_bytes))
        presentation_variables["qr_code"] = d
    except Exception:
        presentation_variables["qr_code"] = None

    header_format = doc_root["presentation_metadata"]["header"]
    seed = doc_root["seed"]

    title = build_section_title(header_format, presentation_variables)
    footer_format = str(doc_root["presentation_metadata"]["footer"])
    answer_key_footer_format = str(doc_root["presentation_metadata"]["answer_key_footer"])
    build_section(c, title, seed, doc_root, footer_format, answer_key_footer_format, presentation_variables)

    c.save()
    
    # # Basic schema check
    # required = {"word", "definition", "part_of_speech", "sentence"}
    # for i, e in enumerate(entries):
    #     if not isinstance(e, dict) or not required.issubset(e.keys()):
    #         print(f"Error: entry {i} missing required keys {required}.", file=sys.stderr)
    #         sys.exit(1)

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

    if not isinstance(doc_root, dict) or not doc_root:
        print("Error: JSON must be a non-empty dictionary.", file=sys.stderr)
        sys.exit(1)

    # Basic schema check
    # required = {"title", "seed", "sections"}
    # TODO: add root schema checking
    
    build_pdf(doc_root, sys.stdout.buffer)

if __name__ == "__main__":
    main()

