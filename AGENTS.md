# AGENTS.md

This file provides agent-agnostic documentation for the Homework Hero (vocab-hunters) project.

## Project Overview

Homework Hero (vocab-hunters) is a vocabulary worksheet generation system that creates customized PDF learning materials with AI-generated sentence completion tasks. It uses a pipeline architecture where vocabulary data flows through validation, extraction, AI generation, and PDF rendering stages.

## Architecture

### Retiring the "phase" scripts

The `Scripts/phase*.py` naming is a fossil of the project's original design: standalone CLI programs chained together with stdout/stdin pipes. **The Flask app is the only supported entry point going forward.** Phase scripts are being migrated one at a time into properly named modules under `Libraries/`, with their CLI plumbing (`argparse`, stdin readers, `main()`, `if __name__ == "__main__"`) deleted.

Migrated so far:
- Phase 4 → `Libraries/sentence_generation.py`

Do not add new CLI entry points, and do not restore the ones that were removed.

### Pipeline Stages

1. **Phase 2** (`Scripts/phase2.py`): Request validation, worksheet ID generation via bit-packing, and cache orchestration. On a cache miss, Phase 2 internally calls Phase 3 then `generate_sentences()`, writes the result to the cache, and returns the generated JSON. On a cache hit, it reads directly from disk. Entry point: `run_with_json()` / `run_from_json()`

2. **Phase 3** (`Scripts/phase3.py`): Extracts vocabulary words, parts of speech, and definitions from source datasets. Builds a doc-level checksum over the vocabulary content. Called by Phase 2 on cache misses; also called directly by Flask for custom-theme generation.

3. **Sentence generation** (`Libraries/sentence_generation.py`): Calls the OpenAI API with theme context and reading level interpolation, then reconciles the response against the payload by checksum and attaches `output.subtitle` plus per-entry `output.sentence`. Dict in, dict out; it never writes to the cache — callers own persistence. Entry point: `generate_sentences()`. Raises `SentenceGenerationError`.

4. **Phase 5** (`Scripts/phase5.py`): Generates PDF with word bank, questions, and answer key using ReportLab. Entry point: `run_with_json()`

The legacy CLI flow (`phase2 | phase5`) still runs but is **unsupported and being removed**. Phase 4 is already Flask-only; the remaining phases will follow.

### Flask App (`app.py`)

The web app imports the remaining phase scripts plus `Libraries/sentence_generation.py`. Two distinct generation flows exist:

**Standard theme flow** (`POST /generate`, `POST /fetch_episode`, `GET /worksheet_pdf`): calls Phase 2 (which orchestrates Phase 3 and `generate_sentences()` internally) then Phase 5.

**Custom-theme flow** (`POST /generate` with `theme=user_specified`): bypasses Phase 2 entirely, calling Phase 3 → `generate_sentences()` → Phase 5 directly with a user-supplied theme string. Custom-theme responses are not cached in the standard datastore.

**Routes (public):**
- `GET /` - Landing page explaining Homework Hero
- `GET /worksheets` - Worksheet generator UI
- `GET /worksheet` - Viewer for a specific worksheet by ID (renders `viewer.html`)
- `GET /worksheet_pdf` - Generates and streams PDF for a worksheet ID
- `GET /about` - About page
- `POST /generate` - Create new worksheet PDF (standard, custom-theme, or per-user)
- `POST /fetch_episode` - Retrieve a specific cached episode as PDF
- `GET /sections/<dataset>` - List available sections for a dataset (global or `u--` user dataset)
- `GET /episodes` - List cached episodes for given parameters

**Routes (auth, in `auth.py` blueprint):**
- `GET/POST /register`, `GET/POST /login`, `POST /logout`, `GET/POST /account`

**Routes (login required, per-user content):**
- `GET /my/worksheets` - Saved themes, uploaded vocabulary sets, cached worksheets
- `GET /my/worksheet` - Viewer for a user worksheet (param-addressed, no packed ID)
- `GET /my/worksheet_pdf` - Streams a cached user worksheet (404 on cache miss; never generates)
- `GET /my/episodes`, `GET /my/themes`, `GET/POST /my/datasets`

**Templates:**
- `templates/landing.html` - Landing page
- `templates/generator.html` - Worksheet generator (theme switching, PDF preview)
- `templates/viewer.html` - Worksheet viewer (episode navigation, PDF embed; serves both global and `/my/` modes via `viewer.mode`)
- `templates/about.html` - About page
- `templates/login.html`, `register.html`, `account.html`, `my_worksheets.html` - Auth and per-user pages
- `templates/_authnav.html` - Login/logout nav partial included in page footers

### Authentication and Per-User Content

Authentication is additive: every pre-existing route works anonymously exactly as before; logging in unlocks persistence.

- **Identity**: `auth.py` blueprint; users stored in SQLite at `{VOCAB_HUNTERS_DB_PATH}/auth.sqlite3` (stdlib `sqlite3`, WAL mode, `PRAGMA user_version` migrations). Passwords hashed with Werkzeug PBKDF2-SHA256 (600k iterations; scrypt unavailable on some Python 3.9 builds).
- **Sessions**: Flask signed-cookie sessions. `session` holds only `user_id` and `sgen` (a copy of the user's `session_generation`; bumping the column on password change invalidates all other sessions). `VOCAB_HUNTERS_SECRET_KEY` env var is required (or `HOMEWORK_HERO_DEV=1` for local dev).
- **CSRF**: HTML form POSTs carry a per-session token; JSON fetch POSTs rely on `SameSite=Lax` + the JSON content-type preflight requirement (documented in `auth.py`).
- **Per-user storage** (`Libraries/user_data.py`): each user gets `{db}/users/{user_id}/` with `user_themes/`, `source_datasets/`, and `responses_datastore/` mirroring the global layout. User-owned items surface in the UI/API with a `u--` key prefix so they can never collide with global key_names. `user_id` always comes from the session, never from request input.
- **Per-user generation** (`Libraries/user_pipeline.py`): `generate_user_worksheet()` mirrors Phase 2's cache orchestration but roots the cache in the user's datastore and calls Phase 3 → `generate_sentences()` directly. User content is not representable in the bit-packed worksheet ID, so payloads keep `worksheet_id=None` and are addressed by explicit params on `/my/*` routes.
- **Custom themes**: logged-in users' custom themes are saved to their own tree and their worksheets are cached/replayable; the anonymous custom flow (global `user_themes/{stem}.txt`, uncached) is unchanged.

### Configuration

Set `VOCAB_HUNTERS_DB_PATH` environment variable to point to the homework hero database directory. Expected subdirectories:
- `source_datasets/` - Vocabulary dataset JSON files
- `themes/` - Theme context files
- `user_themes/` - Anonymous custom-theme files (legacy flow)
- `responses_datastore/` - Cache directory for AI responses
- `reference_data/` - Directory containing `source_datasets.json`, `themes.json`, `models.json`
- `users/` - Per-user content trees (`users/{user_id}/...`), created on demand
- `auth.sqlite3` - SQLite identity database, created on first startup
- `prompt.txt` - System prompt for AI generation

Other environment variables:
- `VOCAB_HUNTERS_SECRET_KEY` (required in production) - session cookie signing key; generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `HOMEWORK_HERO_DEV=1` - local-dev escape hatch when `VOCAB_HUNTERS_SECRET_KEY` is unset
- `SESSION_COOKIE_SECURE=0` - allow session cookies over plain HTTP for local dev (defaults to secure-only)
- `OPENAI_API_KEY` - required for sentence generation; `NTFY_TOPIC` - optional notifications

### Response Caching

Responses are stored in a hierarchical filesystem structure keyed by request parameters:
```
{responses_datastore}/{dataset}/{reading_level}/{section}/{theme}/{model}/{seed}.json
```

The cache path is determined by the request fields, not by content checksums. Checksums (stored inside the cached JSON as `doc_checksum` and per-entry `checksum`) validate that the cached content matches the source vocabulary — they do not determine the path. A cached file at a given path is therefore tied to the specific reference-data ordering in effect when it was written; reordering `source_datasets.json`, `themes.json`, or `models.json` can change worksheet IDs but does not invalidate existing cache files (the path uses string key names, not indices).

### Libraries

- `Libraries/reference_data.py` - Database path resolution and reference data management
- `Libraries/datasets.py` - Dataset file loading utilities
- `Libraries/sentence_generation.py` - OpenAI sentence generation (formerly Phase 4)

## Development Environment

The project virtual environment is located at `venv/`. Run Python tooling through its executable without requiring shell activation:

```bash
venv/bin/python -m pytest tests/
```

## Coding Conventions

### Python Style
- Python 3; the remaining phase scripts include `#!/usr/bin/env python3`. New `Libraries/` modules are plain importable modules — no shebang, not executable.
- `snake_case` for functions and variables; `UPPER_CASE` for module-level constants
- Always open files with `encoding="utf-8"`; always serialize JSON with `ensure_ascii=False, indent=2`
- Prefer `pathlib.Path` over `os.path` for filesystem operations

### Module Structure
- New and migrated code exposes a single well-named function taking and returning Python objects (dicts), e.g. `generate_sentences()` in `Libraries/sentence_generation.py`. Do not add `main()`, `argparse`, or stdin/stdout plumbing.
- The surviving phase scripts still follow the old dual-entry pattern (`main()` for CLI, `run_with_json()` / `run_from_json()` taking and returning JSON strings). Treat that as legacy shape to be migrated, not a pattern to copy.

### Error Handling
- Raise a typed module exception (e.g., `Phase2Error`, `SentenceGenerationError`) so callers can catch without killing the process
- Do not swallow exceptions silently; log at `debug` level before re-raising
- **Current state**: Phase 3 still raises `SystemExit` from some library-reachable code paths. Phase 2 defensively catches those `SystemExit` calls and re-raises as `Phase2Error` before they reach Flask. New code must raise typed exceptions; do not extend the `SystemExit` pattern.

### Logging
- Use the `get_logger()` pattern to return `current_app.logger` inside a Flask request context, falling back to `logging.getLogger(__name__)` outside one
- Log at `debug` level around external calls (OpenAI, cache reads/writes)

### Type Hints
- Add type hints to new functions; backfill existing functions when touching them
- Use `Optional[str]` / `Dict[str, Any]` from `typing` (project targets Python 3.9)

### Cleanliness
- Do not leave commented-out code in committed files; ask the user if it can be deleted and do if confimred. Mention it in the commit message

## Testing Policy

This policy applies to **code changes going forward**. Existing untested code is grandfathered — there is no mandate to retrofit tests onto it.

- New development should be unit-testable wherever it reasonably can be. Introduce `pytest` tests for new code, and modify existing tests when you change behavior they cover.
- Prefer designing for testability (dependency injection, pure functions, separating I/O from logic) over claiming an exemption. Do not contort code or write vacuous tests just to satisfy the policy.
- When code genuinely cannot be unit-tested, document why, using greppable markers:
  - **File level** — when the module (or a meaningful portion of it) can't be unit-tested, add a header comment near the top of the file:
    ```python
    # TESTABILITY: Phase 5 renders PDFs via ReportLab; output is binary/visual
    # and validated manually, not unit-tested.
    ```
  - **Function level** — each public-facing function that cannot be tested carries a single concise comment line stating why:
    ```python
    def stream_pdf(...):
        # not-unit-testable: streams a live OpenAI response to the socket
        ...
    ```
- Use the exact prefixes `TESTABILITY:` (file header) and `not-unit-testable:` (function line) so exemptions can be found with a simple grep.

## Definition of Done

- Exercise the Flask `/generate` route and confirm a PDF is produced without errors
- Open the generated PDF and verify word bank, sentence completion questions, and answer key render correctly with no obvious formatting regressions
- If sentence generation (OpenAI) was changed, verify cached responses still load and new responses are written to the correct filesystem path
- Summarize changed files and any risks to the stage-to-stage JSON contract or cache structure
- Run `venv/bin/python -m pytest tests/` (see `requirements-dev.txt`) — covers auth, per-user content, dataset upload (with generation and PDF rendering mocked), and `Libraries/sentence_generation.py`; extend it when touching those areas. Phases 2/3/5 are still untested.

## Known Traps

### `SystemExit` in library-callable code silently becomes a Flask 500
The remaining phase scripts use `raise SystemExit(message)` for CLI errors, but when the same code is called from Flask (via `run_with_json()`), Flask catches `SystemExit` and returns a 500 with no useful message. Always raise a typed exception (e.g., `Phase2Error`, `SentenceGenerationError`) in any code path reachable from Flask.

### ReportLab layout changes can cascade across pages
Phase5's PDF layout uses tightly coupled pixel math — font sizes, margins, word bank height, and per-question line heights all affect vertical flow across pages. Changing any layout constant can shift content onto the wrong page or clip elements. Test with a real PDF and inspect all three pages (questions p1, questions p2, answer key) after any layout change.

### OpenAI Responses API is not supported by all models
`Libraries/sentence_generation.py` uses `client.responses.parse()` with `text_format=JsonOutputFormat` (structured output). This API and structured output mode are only available on newer OpenAI models. Adding a model to `models.json` that doesn't support the Responses API will fail at runtime with a cryptic SDK error.

### Do not overwrite cached payloads without preserving the full request metadata
The cache path is keyed by request parameters (`dataset/reading_level/section/theme/model/seed.json`). The same parameter combination always maps to the same path, making generation idempotent by default. If you manually write or patch a cached file, the `doc_checksum` and per-entry `checksum` fields inside must remain consistent with the source vocabulary data, or the checksum reconciliation in `Libraries/sentence_generation.py` will reject the file on the next run.

## Security and Safety

### Never commit secrets
- `OPENAI_API_KEY` must be set as an environment variable, never hardcoded or committed
- `VOCAB_HUNTERS_DB_PATH` and its contents (especially `responses_datastore/`) should stay outside the repo; cached AI responses may contain copyrighted or sensitive source material

### Path traversal via request parameters
Cache paths are constructed directly from request fields (`source_dataset`, `theme`, `model`, `section`, `seed`). All web routes validate these through `validate_key_component()` (`Libraries/reference_data.py`, allowlist `[A-Za-z0-9_-]`) before any path construction — apply it to every new route that turns request input into a path component.

### Prompt injection via theme files
Theme file contents are passed verbatim to the OpenAI API as part of the user input. A malicious or malformed theme file (particularly in `user_themes/`) could manipulate model output. Treat user-supplied theme files as untrusted content.

### Authentication boundaries
Anonymous routes (`/generate`, `/fetch_episode`, etc.) remain open by design; they only touch global content. Anything user-owned must go through `/my/*` routes guarded by `login_required`, with the user id taken from the session — never accept a user id from request input. `u--`-prefixed keys from anonymous requests must be rejected (the `/generate` handler returns 401).

### Keep generated user files out of source control
User-created themes (`user_themes/`) and cached responses (`responses_datastore/`) should not be versioned unless intentionally shared. Ensure `.gitignore` excludes these directories.
