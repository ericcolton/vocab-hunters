---
name: review-and-commit
description: Review the current uncommitted diff against homework-hero's project conventions (AGENTS.md/CLAUDE.md), run the test suite, delegate a correctness/simplification pass to the code-review skill, then propose a commit and wait for approval before committing. Only invoke when the user explicitly says they're ready to commit (e.g. "let's commit this", "ready to commit", "review and commit this").
---

# Review and Commit (homework-hero)

## When to invoke this skill

**Only when the user explicitly states they are ready to commit** — e.g.
"let's commit this", "I'm ready to commit", "review and commit this",
"ship it". Do **not** invoke this proactively just because a code change or
task was just finished; finishing a change is not, by itself, a request to
review or commit it. If you're not sure whether the user means "commit now,"
ask rather than invoking the skill speculatively.

Reviews the working tree's pending changes against this project's own written
conventions, runs tests, gets a second opinion on correctness/simplification,
and only then proposes a commit. **Never commits without explicit user
approval of the specific commit**, per this project's git safety protocol —
that holds even though invoking this skill is itself a request to review and
commit.

## 0. Scope the diff

Run `git status` and `git diff` (staged + unstaged) to see what's pending. If
there are no changes, say so and stop — do not proceed or invent work.

Note which files changed; use this to decide which checks below apply. Don't
run checks against parts of the codebase the diff doesn't touch.

## 1. Project-convention review

Read the changed files (not just the diff hunks — surrounding context matters
for several of these) and check against `AGENTS.md` / `CLAUDE.md`:

- **No new CLI entry points.** No new `argparse`, `main()`, stdin/stdout
  plumbing, or `if __name__ == "__main__"` blocks, especially in
  `Scripts/phase*.py` or `Libraries/`. Migrated modules (e.g.
  `Libraries/sentence_generation.py`) must stay CLI-free.
- **Module shape.** New/migrated `Libraries/` code exposes a single
  well-named function taking/returning Python objects, not JSON strings.
- **Typed exceptions.** Flask-reachable code raises a typed module exception
  (`Phase2Error`, `SentenceGenerationError`, etc.), never `SystemExit` or a
  silently swallowed exception. Exceptions are logged at `debug` before
  re-raising.
- **Logging.** Uses the `get_logger()` pattern (falls back to
  `logging.getLogger(__name__)` outside a Flask request context), with
  `debug`-level logs around external calls (OpenAI, cache reads/writes).
- **Type hints.** New functions have type hints (`Optional[str]`,
  `Dict[str, Any]` from `typing`, Python 3.9 syntax — no `X | None`).
- **File/JSON conventions.** Files opened with `encoding="utf-8"`; JSON
  serialized with `ensure_ascii=False, indent=2`; `pathlib.Path` over
  `os.path`.
- **No commented-out code.** If present, ask the user whether it can be
  deleted; if confirmed, delete it and mention the deletion in the commit
  message.
- **Testability markers.** Code that genuinely can't be unit-tested carries
  `# TESTABILITY: ...` (file header) or `# not-unit-testable: ...` (function
  line) with a real reason — not used as a way to dodge writing reasonable
  tests.
- **Cache/checksum integrity.** Any change touching response caching or
  checksums preserves the path shape
  `{dataset}/{reading_level}/{section}/{theme}/{model}/{seed}.json` and keeps
  `doc_checksum` / per-entry `checksum` consistent with source vocabulary
  content.
- **Path traversal.** Any new route that turns request input
  (`source_dataset`, `theme`, `model`, `section`, `seed`, etc.) into a path
  component calls `validate_key_component()` from
  `Libraries/reference_data.py`.
- **Auth boundaries.** `user_id` is always taken from the session, never from
  request input. Anonymous routes reject `u--`-prefixed keys. New per-user
  functionality lives under `/my/*` behind `login_required`.
- **Date formats.** Any date embedded in a filename uses `YYYYMMDD`.
- **Secrets.** No hardcoded `OPENAI_API_KEY` or other secrets; nothing under
  `responses_datastore/`, `user_themes/`, or the DB path is being added to
  the commit.

Collect findings as a plain list (file:line where possible). This step is
self-contained — do not skip it even though step 3 also reviews the diff.

## 2. Run the test suite

Run `venv/bin/python -m pytest tests/`. If anything fails, stop here: report
the failures and do not proceed to review-summary/commit steps until the user
has addressed them (or explicitly tells you to proceed anyway).

## 3. Delegate a correctness/simplification pass

Invoke the `code-review` skill on the current diff (default effort) for
correctness bugs and reuse/simplification/efficiency findings. This is in
addition to, not instead of, step 1 — step 1 catches project-specific
convention violations that a generic reviewer won't know about.

## 4. Manual Definition-of-Done reminders

`AGENTS.md`'s Definition of Done includes steps that can't be verified from
here (no running Flask app / browser in this context): exercising `/generate`
and visually inspecting the produced PDF's three pages (questions p1/p2,
answer key), and confirming cached responses still load if sentence
generation changed. If the diff touches `Scripts/phase5.py` (ReportLab
layout), `Libraries/sentence_generation.py`, or cache path/checksum logic,
call this out explicitly as something the user should have verified (or
still needs to) — don't claim it's been checked.

## 5. Summarize and propose the commit — then stop

Present, in one place:
- Files changed
- Findings from steps 1 and 3 (or "none found")
- Any manual-verification reminders from step 4
- The exact commit message you propose, following this repo's existing style
  (see `git log` for tone — concise, present-tense, why-focused) and ending
  with whatever attribution lines the current session's commit-attribution
  system reminder specifies, if any
- The exact `git add` file list you'd stage (never a blanket `git add -A`/`.`)

Then **stop and wait for the user's go-ahead**. Only run `git add`/`git
commit` after they explicitly approve — either this summary or a specific
correction to it. If they raise a blocking finding, fix it, re-run the
affected checks (tests included, if code changed), and present a fresh
summary before asking again.
