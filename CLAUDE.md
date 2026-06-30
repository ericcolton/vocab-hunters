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

### Run Pipeline via CLI
```bash
VOCAB_HUNTERS_DB_PATH=/path/to/database \
  ./Scripts/phase2.py < request.json | ./Scripts/phase5.py > output.pdf
```

Phase 2 internally calls Phase 3 and Phase 4 on a cache miss, so the standard CLI flow is `phase2 | phase5`. Individual phases can still be run standalone for debugging.
