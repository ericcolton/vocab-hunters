# AGENTS.md

This file provides agent-agnostic documentation for the Homework Hero (vocab-hunters) project.

## Project Overview

Homework Hero (vocab-hunters) is a vocabulary worksheet generation system that creates customized PDF learning materials with AI-generated sentence completion tasks. It uses a pipeline architecture where vocabulary data flows through validation, extraction, AI generation, and PDF rendering phases.

## Architecture

### Pipeline Phases

1. **Phase 2** (`Scripts/phase2.py`): Request validation, worksheet ID generation via bit-packing, and cache orchestration. On a cache miss, Phase 2 internally calls Phase 3 then Phase 4, writes the result to the cache, and returns Phase 4-shaped JSON. On a cache hit, it reads directly from disk. Entry point: `run_with_json()` / `run_from_json()`

2. **Phase 3** (`Scripts/phase3.py`): Extracts vocabulary words, parts of speech, and definitions from source datasets. Builds a doc-level checksum over the vocabulary content. Called by Phase 2 on cache misses; also called directly by Flask for custom-theme generation.

3. **Phase 4** (`Scripts/phase4.py`): Calls OpenAI API with theme context and reading level interpolation. Writes the structured response to the cache. Called by Phase 2 on cache misses; also called directly by Flask for custom-theme generation.

4. **Phase 5** (`Scripts/phase5.py`): Generates PDF with word bank, questions, and answer key using ReportLab. Entry point: `run_with_json()`

The **standard CLI flow** is therefore:
```
phase2 | phase5
```
Phase 2 handles the cache orchestration (calling Phase 3 and Phase 4 internally when needed), so piping `phase2 | phase3 | phase4 | phase5` is not the implemented architecture.

### Flask App (`app.py`)

The web app imports all four phases. Two distinct generation flows exist:

**Standard theme flow** (`POST /generate`, `POST /fetch_episode`, `GET /worksheet_pdf`): calls Phase 2 (which orchestrates Phase 3 and Phase 4 internally) then Phase 5.

**Custom-theme flow** (`POST /generate` with `theme=user_specified`): bypasses Phase 2 entirely, calling Phase 3 → Phase 4 → Phase 5 directly with a user-supplied theme string. Custom-theme responses are not cached in the standard datastore.

**Routes:**
- `GET /` - Landing page explaining Homework Hero
- `GET /worksheets` - Worksheet generator UI
- `GET /worksheet` - Viewer for a specific worksheet by ID (renders `viewer.html`)
- `GET /worksheet_pdf` - Generates and streams PDF for a worksheet ID
- `GET /about` - About page
- `POST /generate` - Create new worksheet PDF (standard or custom-theme)
- `POST /fetch_episode` - Retrieve a specific cached episode as PDF
- `GET /sections/<dataset>` - List available sections for a dataset
- `GET /episodes` - List cached episodes for given parameters

**Templates:**
- `templates/landing.html` - Landing page
- `templates/generator.html` - Worksheet generator (theme switching, PDF preview)
- `templates/viewer.html` - Worksheet viewer (episode navigation, PDF embed)
- `templates/about.html` - About page

### Configuration

Set `VOCAB_HUNTERS_DB_PATH` environment variable to point to the homework hero database directory. Expected subdirectories:
- `source_datasets/` - Vocabulary dataset JSON files
- `themes/` - Theme context files
- `user_themes/` - User-created theme files
- `responses_datastore/` - Cache directory for AI responses
- `reference_data/` - Directory containing `source_datasets.json`, `themes.json`, `models.json`
- `prompt.txt` - System prompt for AI generation

### Response Caching

Responses are stored in a hierarchical filesystem structure keyed by request parameters:
```
{responses_datastore}/{dataset}/{reading_level}/{section}/{theme}/{model}/{seed}.json
```

The cache path is determined by the request fields, not by content checksums. Checksums (stored inside the cached JSON as `doc_checksum` and per-entry `checksum`) validate that the cached content matches the source vocabulary — they do not determine the path. A cached file at a given path is therefore tied to the specific reference-data ordering in effect when it was written; reordering `source_datasets.json`, `themes.json`, or `models.json` can change worksheet IDs but does not invalidate existing cache files (the path uses string key names, not indices).

### Libraries

- `Libraries/reference_data.py` - Database path resolution and reference data management
- `Libraries/datasets.py` - Dataset file loading utilities

## Coding Conventions

### Python Style
- Python 3; all scripts include `#!/usr/bin/env python3`
- `snake_case` for functions and variables; `UPPER_CASE` for module-level constants
- Always open files with `encoding="utf-8"`; always serialize JSON with `ensure_ascii=False, indent=2`
- Prefer `pathlib.Path` over `os.path` for filesystem operations (phase4 currently uses `os.path` — treat that as tech debt)

### Phase Script Structure
Each phase script follows a consistent dual-entry pattern:
- `main()` — CLI entry point: reads from stdin, writes to stdout, exits non-zero on error
- `run_with_json()` / `run_from_json()` — library entry point: accepts/returns strings, raises exceptions instead of calling `sys.exit()`

### Error Handling
- In CLI context (`main()`), use `raise SystemExit(message)` for fatal user-facing errors
- In library context (called by Flask or another phase), raise a typed exception (e.g., `Phase2Error`) so callers can catch without killing the process
- Do not swallow exceptions silently; log at `debug` level before re-raising
- **Current state**: Phase 3 and Phase 4 still raise `SystemExit` from some library-reachable code paths. Phase 2 defensively catches those `SystemExit` calls and re-raises as `Phase2Error` before they reach Flask. New code should raise typed exceptions; do not extend the `SystemExit` pattern.

### Logging
- Use the `get_logger()` pattern to return `current_app.logger` inside a Flask request context, falling back to `logging.getLogger(__name__)` for CLI use
- Log at `debug` level around external calls (OpenAI, cache reads/writes)

### Type Hints
- Add type hints to new functions; backfill existing functions when touching them
- Use `Optional[str]` / `Dict[str, Any]` from `typing` (project targets Python 3.9)

### Cleanliness
- Do not leave commented-out code in committed files; delete it or move the rationale to a commit message

## Definition of Done

- Run the full pipeline end-to-end (`phase2 | phase5`) or the Flask `/generate` route and confirm a PDF is produced without errors
- Open the generated PDF and verify word bank, sentence completion questions, and answer key render correctly with no obvious formatting regressions
- If Phase 4 (OpenAI) was changed, verify cached responses still load and new responses are written to the correct filesystem path
- Summarize changed files and any risks to the phase-to-phase JSON contract or cache structure
- **Note: no automated test suite exists** — consider adding one (pytest with mocked OpenAI responses and a fixture dataset would cover the core pipeline)

## Known Traps

### `SystemExit` in library-callable code silently becomes a Flask 500
Phase scripts use `raise SystemExit(message)` for CLI errors, but when the same code is called from Flask (via `run_with_json()`), Flask catches `SystemExit` and returns a 500 with no useful message. Always raise a typed exception (e.g., `Phase2Error`) in any code path reachable from the library entry points.

### ReportLab layout changes can cascade across pages
Phase5's PDF layout uses tightly coupled pixel math — font sizes, margins, word bank height, and per-question line heights all affect vertical flow across pages. Changing any layout constant can shift content onto the wrong page or clip elements. Test with a real PDF and inspect all three pages (questions p1, questions p2, answer key) after any layout change.

### OpenAI Responses API is not supported by all models
Phase4 uses `client.responses.parse()` with `text_format=JsonOutputFormat` (structured output). This API and structured output mode are only available on newer OpenAI models. Adding a model to `models.json` that doesn't support the Responses API will fail at runtime with a cryptic SDK error.

### Do not overwrite cached payloads without preserving the full request metadata
The cache path is keyed by request parameters (`dataset/reading_level/section/theme/model/seed.json`). The same parameter combination always maps to the same path, making generation idempotent by default. If you manually write or patch a cached file, the `doc_checksum` and per-entry `checksum` fields inside must remain consistent with the source vocabulary data, or phase4's checksum validation will reject the file on the next run.

## Security and Safety

### Never commit secrets
- `OPENAI_API_KEY` must be set as an environment variable, never hardcoded or committed
- `VOCAB_HUNTERS_DB_PATH` and its contents (especially `responses_datastore/`) should stay outside the repo; cached AI responses may contain copyrighted or sensitive source material

### Path traversal via request parameters
Cache paths are constructed directly from request fields (`source_dataset`, `theme`, `model`, `section`, `seed`). If these values come from untrusted input, a crafted value (e.g., `../../etc`) could escape the datastore root. Validate or sanitize these fields before using them as path components.

### Prompt injection via theme files
Theme file contents are passed verbatim to the OpenAI API as part of the user input. A malicious or malformed theme file (particularly in `user_themes/`) could manipulate model output. Treat user-supplied theme files as untrusted content.

### Flask app has no authentication
The `/generate` and `/fetch_episode` routes are unauthenticated. The app is designed for local or trusted-network use. Do not expose it publicly without adding an auth layer.

### Keep generated user files out of source control
User-created themes (`user_themes/`) and cached responses (`responses_datastore/`) should not be versioned unless intentionally shared. Ensure `.gitignore` excludes these directories.
