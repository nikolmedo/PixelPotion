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
config/                 # hostapd/dnsmasq configs, systemd unit, sudoers whitelist — deployed by install.sh
install.sh / update.sh  # Pi provisioning and GitHub-release auto-update (not unit-tested)
deploy-files.txt        # Runtime files both scripts copy to the Pi — single source of truth
tests/                  # Pytest suite — see "Testing" below
```

## Architecture & Data Flow

```text
button press (GPIO thread) / POST /capture (request thread)
  └─ capture_to_pending(style_id)                       [app.py, camera_lock only]
       ├─ capture_photo()        → Picamera2 → photos/original/photo_<ts>.jpg
       ├─ ensure_in_pending()    → temp file + fsync + os.replace → photos/pending/
       │                           (fails → "Error: could not save photo", stop)
       ├─ update_photo_state()   → sidecar photos/pending/<photo>.jpg.json {style_id}
       └─ enqueue_pending(name)  → work_queue (de-duplicated by name)

worker thread (ONE, started by main) → process_next() → process_pending_photo(name)
  ├─ is_wifi_connected()         → offline? stop, photo stays pending
  ├─ AI step, skipped if the sidecar's processed_path still exists
  │    process_with_ai() → ai_provider.process_image_result() → AIResult
  │    permanent failure → sidecar failed + failed_reason, stop
  ├─ send_telegram_photo(original) unless telegram_original_sent
  ├─ send_telegram_photo(styled)   unless telegram_styled_sent
  └─ remove_from_pending()       → photo + sidecar, only after both sends

auto_retry_loop (every 300s)     → enqueue retry_candidates() (pending, not failed)
/process_photo, /process_all     → request_processing() (clear failed, set style) + enqueue
```

**The durability contract is the heart of the app:**

- A photo is durable once `ensure_in_pending()` returns True. Nothing reaches the AI
  step without that copy.
- It leaves `photos/pending/` (with its sidecar) only after **both** Telegram messages
  were delivered. Any other outcome leaves it queued: no WiFi, AI error, Telegram
  error, an exception, or a restart.
- The sidecar records progress so a retry resumes. The styled image is reused (no
  second AI charge), and only the unsent Telegram messages go out (no duplicates).
  A missing or unreadable sidecar means "start from scratch with the active style",
  which is how photos queued by older versions are handled.
- Permanent AI failures (rejected key, safety block, rejected input) set `failed`.
  Auto-retry skips such photos; they stay pending, the gallery shows
  `Failed: <reason>`, and a manual Process (single or all) clears the flag.

Don't break this.

## Key Design Decisions & Gotchas

- **Lazy hardware imports.** `picamera2`, `RPi.GPIO`, `requests`, and `google.genai` are
  imported *inside* functions, never at module level. This keeps the app importable
  (and testable) off-device. Preserve this pattern when touching those functions.
- **Module-level global state.** `app.config` (dict) and `app.status` are shared by
  reference across routes and threads — mutate them in place, never rebuild/reassign them.
- **Concurrency model.**
  - `camera_lock`: one capture at a time. Capturing never waits for processing.
  - `config_lock` (re-entrant): every read-modify-write of `config` plus `save_config()`.
  - One worker thread drains `work_queue`, so photos are processed one at a time. The
    queue de-duplicates by name, so a photo is never queued twice.
  - `status_lock`: always use `update_status()` / `status_snapshot()`, never `status[...]`.
  - `_photo_state_lock`: sidecar writes vs. photo deletion. A photo deleted
    mid-processing never gets its sidecar written back.
- **Import-time side effects in `app.py`.** Logging attaches a `FileHandler` for
  `/home/pi/pixelpotion/pixelpotion.log` and photo directories are created on import.
  Off-device this path doesn't exist — `tests/conftest.py` stubs `logging.FileHandler`
  *before* importing `app`. Keep that in mind if you reorganize imports.
- **Config layering.** `default_config.json` (factory, in git) is overlaid by
  `config.json` (runtime, gitignored). `load_config()` merges them over a deep copy of
  the defaults; empty `styles` or `active_style_id` fall back to defaults. An unreadable
  `config.json` is moved to `config.json.corrupt-<timestamp>` and defaults are loaded.
  `save_config()` writes atomically (`write_json_atomic`: temp + fsync + `os.replace`).
- **Camera profiles.** `CAMERA_PROFILES` in `app.py` holds per-sensor controls: IMX708
  needs fixed AWB gains (`ColourGains (1.0, 2.5)`) to avoid a red tint; IMX219 uses auto AWB.
- **Pending filenames are untrusted.** Any route or helper that turns a portal-supplied
  filename into a path must go through `_resolve_pending()`. It accepts only a bare
  `*.jpg` name inside `photos/pending/` and returns `None` otherwise, so sidecars
  and temp files are never addressable.
- **Every POST needs a CSRF token.** A `before_request` hook rejects POSTs whose
  `csrf_token` form field or `X-CSRF-Token` header doesn't match the session token.
  New `<form method="post">` blocks must include
  `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`; new `fetch()` POSTs
  must send the `X-CSRF-Token` header (templates expose `CSRF_TOKEN` via `|tojson`).
  Endpoints called with fetch that expect JSON belong in `JSON_ENDPOINTS`. In tests, the
  `client` fixture sends a valid token automatically; use `plain_client` to test rejection.
- **Secrets are never rendered.** The WiFi password, Gemini key, and Telegram bot token
  never go back into HTML. A blank secret field on `/save_config` or `/save_wifi` keeps
  the stored value (WiFi: only when the SSID is unchanged; a new SSID with a blank
  password is saved as an open network).
- **Secrets at rest stay private.** `config.json` is chmod 0600 on every save; the WiFi
  PSK is piped to `sudo tee` (never on a command line or in a temp file) and
  `wpa_supplicant.conf` is kept 0600 by install.sh; logged
  Telegram exceptions have the bot token redacted (requests embeds the URL in errors).
- **Unprivileged service + sudo whitelist.** `pixelpotion.service` runs as `pi`
  (groups `video gpio netdev`). Network management (`connect_wifi`, `start_ap_mode`,
  `/scan_wifi`) goes through `_run_privileged()` → `sudo -n <exact command>`, and
  every command line must appear verbatim in `config/pixelpotion.sudoers`
  (installed as `/etc/sudoers.d/pixelpotion`). Root-owned files are written by piping
  content to `sudo /usr/bin/tee <path>` — never `sudo bash -c`, never a temp file.
  Adding a privileged call means adding the exact line to the sudoers file;
  `tests/test_app_network.py::TestSudoersWhitelist` fails otherwise. Caveat: writing
  `/etc/dhcpcd.conf` is still root-equivalent in theory (dhcpcd's `script` option),
  so the whitelist narrows privilege rather than eliminating it; the real fix is the
  planned NetworkManager migration.
- **Failures degrade, never crash.** Hardware/network helpers (`capture_photo`,
  `is_wifi_connected`, `send_telegram_photo(s)`, `process_image`) return `None`/`False`
  on failure instead of raising; `process_with_ai` returns a failed `AIResult`.
  Callers rely on this. `capture_photo` always stops and closes the camera.
- **AI error classes** (`ai_provider.py`):
  - 401/403 fail permanently at once.
  - 400/404 try the next model once, without retries.
  - 429/5xx retry with backoff, honoring `Retry-After` / `retryDelay`, capped at
    `MAX_RETRY_DELAY_SECONDS`.
  - A safety block (no candidates or content, a safety finish reason, a prompt
    `block_reason`) is permanent.

## Adding an AI Provider

`ai_provider.py` is the only file involved:

1. Implement `_process_with_<name>(image_path, prompt, api_key) -> AIResult`. Return
   the processed file path on success, or `AIResult(permanent=..., reason=...)` on
   failure; never raise to the caller. A plain `str | None` return is also accepted
   and treated as success / transient failure.
2. Register it in the `_PROVIDERS` dict.
3. Switch `AI_PROVIDER` in `constants.py`.

## Testing

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
.venv/Scripts/python -m pytest                                # 230 tests (7 POSIX/bash-only, skipped on Windows), ~3s
```

- Suite layout mirrors the layers: `test_constants`, `test_ai_provider`, `test_app_config`,
  `test_app_network`, `test_app_camera`, `test_app_telegram`, `test_app_pipeline`,
  `test_app_routes` (Flask test client), plus `test_deploy_manifest` (static checks of
  what install.sh/update.sh deploy).
- CI (`.github/workflows/ci.yml`) runs the suite on Python 3.11 and 3.13, `shellcheck
  -S warning` on both scripts, and `visudo -cf` on the sudoers file.
- `tests/conftest.py` is the linchpin: it stubs `logging.FileHandler` before importing
  `app`, and its autouse `isolated_state` fixture redirects all paths to `tmp_path` and
  resets every global (config, status, processing queue, cached Gemini client)
  between tests. Tests never start the worker thread: they call `process_next(block=False)`
  or `process_pending_photo()` directly, so the suite stays synchronous.
- Hardware/SDK modules are injected as `MagicMock`s via `sys.modules` — never add
  `RPi.GPIO`, `picamera2`, or `google-genai` to `requirements-dev.txt`.
- Test conventions: English only, Arrange-Act-Assert, realistic data (no `foo`/`bar`),
  assert observable behavior (outputs, files, status) over implementation details.
- Intentionally untested: `auto_retry_loop`, `gpio_button_listener`, `_worker_loop`, `main`.
  They are infinite loops and OS glue whose tests would couple without protecting
  refactors. Their bodies delegate to tested helpers (`retry_candidates`,
  `capture_to_pending`, `process_next`).

## Conventions

- Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`...), no AI attribution lines.
- Logging through the module loggers (`log = logging.getLogger("pixelpotion[...]")`).
- All code, comments, and tests in English.
- New external calls follow the existing pattern: lazy import + degrade to `None`/`False`.

## Deployment Notes

- `install.sh` provisions a Pi (idempotent, needs a `pi` user): apt packages, venv
  (`--system-site-packages`, so apt's picamera2/RPi.GPIO are visible), hostapd/dnsmasq
  AP mode with a random per-device passphrase, the visudo-validated sudoers whitelist,
  and `pixelpotion.service`. `update.sh` pulls the latest GitHub release (only if
  strictly newer, `v` prefix ignored), backs up `config.json`, redeploys code, sudoers
  and unit, reinstalls deps, restarts the service.
- **Adding a runtime file** (module, template, data file): list it in `deploy-files.txt`,
  or it will not reach the Pi. `tests/test_deploy_manifest.py` guards local imports,
  templates, requirements, the service user and the sudoers file.
- Shell scripts and `config/*` must keep LF endings (`.gitattributes` enforces it).
- Live logs on the device: `sudo journalctl -u pixelpotion -f`.
- The web portal binds `0.0.0.0:8080`; AP mode answers at `192.168.4.1`.
