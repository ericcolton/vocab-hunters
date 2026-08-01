# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Commands

### Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Run Flask Development Server
```bash
VOCAB_HUNTERS_DB_PATH=/path/to/database flask --app app run --debug
```

### Run Pipeline via CLI (legacy — unsupported)

The pipe-based CLI is a fossil of the project's original design and is being retired in favor of the Flask app. Phase 4 has already been migrated to `Libraries/sentence_generation.py` with its CLI removed; the remaining phases will follow. Do not add new CLI entry points.

```bash
VOCAB_HUNTERS_DB_PATH=/path/to/database \
  ./Scripts/phase2.py < request.json | ./Scripts/phase5.py > output.pdf
```

Phase 2 internally calls Phase 3 and `generate_sentences()` on a cache miss, so the legacy flow is `phase2 | phase5`.

### Date formats

Anytime a date format is used in a filename, it should always use YYYYMMDD format (to maintain chronological sorting)

