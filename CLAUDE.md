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
  ./Scripts/phase2.py < request.json | ./Scripts/phase3.py | ./Scripts/phase4.py | ./Scripts/phase5.py > output.pdf
```

Individual phases can be run independently or piped together. Phase 2 can skip to Phase 5 if cached responses exist.
