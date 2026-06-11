# CLAUDE.md

@AGENTS.md

## Quick Reference

- Run tests: `.venv/Scripts/python -m pytest` (Windows dev machine) — must stay green and fast (~1s).
- The app only runs fully on a Raspberry Pi; on dev machines, verify behavior through the test suite, not by launching `app.py`.
- Before changing `full_pipeline`, `ensure_in_pending`, or `remove_from_pending`, re-read the durability contract in AGENTS.md — pending photos must survive every failure mode.
- `tests/conftest.py` patches import-time side effects of `app.py`; if you add module-level code to `app.py`, check the suite still collects.
