# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Homework Hero (vocab-hunters) is a vocabulary worksheet generation system that creates customized PDF learning materials with AI-generated sentence completion tasks. It uses a pipeline architecture where vocabulary data flows through validation, extraction, AI generation, and PDF rendering phases.

## Commands

### Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Run Flask Development Server
```bash
HOMEWORK_HERO_CONFIG_PATH=/path/to/config.json flask --app app run --debug
```

### Run Pipeline via CLI
```bash
HOMEWORK_HERO_CONFIG_PATH=/path/to/config.json \
  ./Scripts/phase2.py < request.json | ./Scripts/phase3.py | ./Scripts/phase4.py | ./Scripts/phase5.py > output.pdf
```

Individual phases can be run independently or piped together. Phase 2 can skip to Phase 5 if cached responses exist.

## Architecture

### Pipeline Phases

1. **Phase 2** (`Scripts/phase2.py`): Request validation, worksheet ID generation via bit-packing, and response cache lookup. Entry point: `run_with_json()`

2. **Phase 3** (`Scripts/phase3.py`): Extracts vocabulary words, parts of speech, and definitions from source datasets. Generates content checksums for cache tracking.

3. **Phase 4** (`Scripts/phase4.py`): Calls OpenAI API with theme context and reading level interpolation. Caches responses by content checksum.

4. **Phase 5** (`Scripts/phase5.py`): Generates PDF with word bank, questions, and answer key using ReportLab. Entry point: `run_with_json()`

### Flask App (`app.py`)

The web app imports Phase 2 and Phase 5 directly, bypassing the CLI pipeline.

**Routes:**
- `GET /` - Landing page explaining Homework Hero
- `GET /worksheets` - Worksheet generator UI
- `GET /about` - About page
- `POST /generate` - Create new worksheet PDF
- `POST /fetch_episode` - Retrieve cached worksheet
- `GET /sections/<dataset>` - List available sections
- `GET /episodes` - List cached episodes for given parameters

**Templates:**
- `templates/landing.html` - Landing page
- `templates/generator.html` - Worksheet generator (theme switching, PDF preview)
- `templates/about.html` - About page

### Configuration

Set `HOMEWORK_HERO_CONFIG_PATH` environment variable to point to a config JSON file. Config keys:
- `source_datasets` - Path to vocabulary dataset JSON files
- `prompt_path` - System prompt for AI generation
- `themes_dir` - Theme context files directory
- `responses_datastore` - Cache directory for AI responses
- `reference_data` - Directory containing `source_datasets.json`, `themes.json`, `models.json`

Example configs in `Config/` directory.

### Response Caching

Responses are stored in a hierarchical filesystem structure:
```
{responses_datastore}/{dataset}/{reading_level}/{section}/{theme}/{model}/{episode}.json
```

Content checksums ensure idempotent caching - identical vocabulary content produces identical cache keys.

### Libraries

- `Libraries/reference_data.py` - Config loading and reference data management
- `Libraries/datasets.py` - Dataset file loading utilities
