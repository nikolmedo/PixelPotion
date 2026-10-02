# CLAUDE.md

@AGENTS.md

## Quick Reference

- Run tests: `.venv/Scripts/python -m pytest` (Windows dev machine) — must stay green and fast (~5s).
- The app only runs fully on a Raspberry Pi; on dev machines, verify behavior through the test suite, not by launching `app.py`.
- Before changing `capture_to_pending`, `ensure_in_pending`, `process_pending_photo`, or `remove_from_pending`, re-read the durability contract in AGENTS.md. Pending photos must survive every failure mode.
- `tests/conftest.py` patches import-time side effects of `app.py`; if you add module-level code to `app.py`, check the suite still collects.
