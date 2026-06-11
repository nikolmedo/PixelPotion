# AGENTS.md — PixelPotion Technical Guide

Instructions for AI coding agents and human contributors working on this codebase.
For the user-facing overview, see [README.md](README.md).

## Project Overview

PixelPotion is a Raspberry Pi AI camera: a GPIO button (or the web portal) captures a photo,
Google Gemini restyles it, and both images are delivered via Telegram. Photos taken offline
persist in a pending queue and are retried automatically. A Flask web portal on port 8080
handles configuration, style management, and the pending gallery.

**Stack:** Python 3 · Flask · Pillow · google-genai SDK · Picamera2 · RPi.GPIO
**Target:** Raspberry Pi Zero 2 W running as a systemd service (`pixelpotion.service`)
**Dev machines:** anything — the test suite mocks all hardware and external APIs.

## Repository Map

```text
app.py                  # Everything app-level: Flask routes, pipeline, camera, WiFi, Telegram, GPIO
ai_provider.py          # AI provider abstraction — Gemini today, designed for OpenAI/Anthropic later
constants.py            # Tuning values (models, retries, timeouts) + loads default_config.json
default_config.json     # Factory defaults: AP credentials, built-in styles, GPIO pin
config.json             # Runtime config (API keys, WiFi) — gitignored, created at runtime
templates/              # Jinja2 templates: index (portal), styles (CRUD), gallery (pending queue)
config/                 # hostapd/dnsmasq configs + systemd unit, deployed by install.sh
install.sh / update.sh  # Pi provisioning and GitHub-release auto-update (not unit-tested)
tests/                  # Pytest suite — see "Testing" below
```

## Architecture & Data Flow

```text
button press / POST /capture
  └─ full_pipeline(photo_path=None, style_id=None)        [app.py]
       ├─ capture_photo()            → Picamera2 → photos/original/
       ├─ ensure_in_pending()        → copy to photos/pending/ (durability)
       ├─ is_wifi_connected()        → offline? stop here, photo stays pending
       ├─ process_with_ai()          → ai_provider.process_image()
       │    └─ Gemini: model fallback chain × MAX_RETRIES, exponential backoff
       │       output → photos/processed/styled_*.jpg
       ├─ send_telegram_photos()     → Telegram Bot API (original + styled)
       └─ remove_from_pending()      → only after successful delivery

auto_retry_loop (daemon thread)      → re-runs pipeline for pending photos every 300s
gpio_button_listener (daemon thread) → debounced GPIO edge → pipeline in a new thread
```

**The durability contract is the heart of the app:** a photo only leaves
`photos/pending/` after Telegram delivery succeeds. Any failure (no WiFi, AI error,
Telegram error) must leave it queued for retry. Don't break this.

## Key Design Decisions & Gotchas

- **Lazy hardware imports.** `picamera2`, `RPi.GPIO`, `requests`, and `google.genai` are
  imported *inside* functions, never at module level. This keeps the app importable
  (and testable) off-device. Preserve this pattern when touching those functions.
- **Module-level global state.** `app.config` (dict) and `app.status` are shared by
  reference across routes and threads — mutate them in place, never rebuild/reassign them.
  Concurrency is guarded by `processing_lock` (pipeline) and `camera_lock` (capture).
- **Import-time side effects in `app.py`.** Logging attaches a `FileHandler` for
  `/home/pi/pixelpotion/pixelpotion.log` and photo directories are created on import.
  Off-device this path doesn't exist — `tests/conftest.py` stubs `logging.FileHandler`
  *before* importing `app`. Keep that in mind if you reorganize imports.
- **Config layering.** `default_config.json` (factory, in git) is overlaid by
  `config.json` (runtime, gitignored). `load_config()` merges them; empty `styles` or
  `active_style_id` fall back to defaults.
- **Camera profiles.** `CAMERA_PROFILES` in `app.py` holds per-sensor controls: IMX708
  needs fixed AWB gains (`ColourGains (1.0, 2.5)`) to avoid a red tint; IMX219 uses auto AWB.
- **Failures degrade, never crash.** Hardware/network helpers (`capture_photo`,
  `is_wifi_connected`, `send_telegram_photos`, `process_image`) return `None`/`False`
  on failure instead of raising. Callers rely on this.

### Known Issues

- **Latent encoding bug:** `save_config()`/`load_config()` open `config.json` without
  `encoding="utf-8"`. Works on the Pi (UTF-8 locale) but crashes with emoji on
  cp1252 systems. Documented by the `xfail` test
  `tests/test_app_config.py::TestSaveConfig::test_round_trip_preserves_emoji_content` —
  it will XPASS once `encoding="utf-8"` is added to both `open()` calls.

## Adding an AI Provider

`ai_provider.py` is the only file involved:

1. Implement `_process_with_<name>(image_path, prompt, api_key) -> str | None`
   (returns the processed file path, or `None` on failure — never raise to the caller).
2. Register it in the `_PROVIDERS` dict.
3. Switch `AI_PROVIDER` in `constants.py`.

## Testing

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
.venv/Scripts/python -m pytest                                # 84 tests, ~1s
```

- Suite layout mirrors the layers: `test_constants`, `test_ai_provider`, `test_app_config`,
  `test_app_network`, `test_app_camera`, `test_app_telegram`, `test_app_pipeline`,
  `test_app_routes` (Flask test client).
- `tests/conftest.py` is the linchpin: it stubs `logging.FileHandler` before importing
  `app`, and its autouse `isolated_state` fixture redirects all paths to `tmp_path` and
  resets every global (config, status, cached Gemini client) between tests.
- Hardware/SDK modules are injected as `MagicMock`s via `sys.modules` — never add
  `RPi.GPIO`, `picamera2`, or `google-genai` to `requirements-dev.txt`.
- Test conventions: English only, Arrange-Act-Assert, realistic data (no `foo`/`bar`),
  assert observable behavior (outputs, files, status) over implementation details.
- Intentionally untested: `auto_retry_loop`, `gpio_button_listener`, `start_ap_mode`,
  `main` — infinite loops and OS glue whose tests would couple without protecting refactors.

## Conventions

- Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`...), no AI attribution lines.
- Logging through the module loggers (`log = logging.getLogger("pixelpotion[...]")`).
- All code, comments, and tests in English.
- New external calls follow the existing pattern: lazy import + degrade to `None`/`False`.

## Deployment Notes

- `install.sh` provisions a fresh Pi: apt packages, venv, hostapd/dnsmasq AP mode,
  enables `pixelpotion.service`. `update.sh` pulls the latest GitHub release, backs up
  `config.json`, reinstalls deps, restarts the service.
- Live logs on the device: `sudo journalctl -u pixelpotion -f`.
- The web portal binds `0.0.0.0:8080`; AP mode answers at `192.168.4.1`.
