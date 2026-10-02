# Changelog

All notable changes to PixelPotion are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [3.2.0] - 2026-10-02

### Added

- `uninstall.sh` removes a v3.x install: the service, hotspot settings and sudo rules.
  Photos and `config.json` are moved to a private backup folder unless you pass `--purge`.
  Your home WiFi connection is kept. See the README, "Uninstalling".
- `uninstall-legacy.sh` does the same for installs from v2.0.2 or earlier, and also deletes
  the WiFi password copy those versions left in `/tmp`. Download it from the repository.
- Optional flags for both: `--remove-packages` (hostapd and dnsmasq) and
  `--restore-boot-config` (the camera lines in `config.txt`). The legacy script also has
  `--remove-pip-packages`.

## [3.1.0] - 2026-10-02

### Changed

- New look for the web portal: a bright, playful design with sticker-style controls and
  bolder, rounder type.
- The Camera page shows each photo "brewing" in a potion bottle that fills as it is
  captured, restyled and sent; the cork pops when it is delivered and the potion turns red
  if something fails.
- Buttons, style choices, gallery photos and the photo preview now animate in response to
  taps. All motion turns off when the phone asks for reduced motion.
- The status message no longer starts with an emoji.

## [3.0.0] - 2026-10-02

Installs from v2.0.2 or earlier must run `install.sh` once from a fresh clone; after that,
`update.sh` handles updates. See the README, "Updating".

### Security

- Every form and request that changes something now needs a per-session CSRF token.
- The settings page no longer shows the saved WiFi password, Gemini key or Telegram token.
  Leaving a secret field blank keeps the saved value.
- The service runs as the `pi` user instead of root. The few network commands it needs go
  through a `sudo` whitelist of exact command lines (`/etc/sudoers.d/pixelpotion`).
- `config.json` and `wpa_supplicant.conf` are readable only by their owner, the WiFi
  password is never written to a temporary file, and the Telegram token is removed from
  logged errors.
- The setup hotspot gets a random password per device instead of the shared
  `pixelpotion123`.
- Gallery actions reject file names outside the photo queue.
- WiFi names and passwords with quotes or line breaks are refused instead of being written
  into the WiFi configuration.
- Fixed places where a style name or WiFi network name could inject script into the portal,
  and an open redirect after an expired session.
- Pillow 12.2 or newer is required (CVE-2026-42309).

### Fixed

- Photos are no longer lost when the button is pressed while another photo is processing.
  Capturing and processing are now separate, and a photo is safely on the SD card before it
  goes anywhere.
- A retry continues where the last attempt stopped: it reuses the styled image (no second
  Gemini charge) and only sends the Telegram messages that did not go out yet.
- Gemini errors that can never succeed (rejected key, safety block, rejected request) are no
  longer retried forever. The photo stays in the gallery marked `Failed: <reason>`.
- Rate limits and server errors wait as long as Gemini asks, up to a minute per retry.
- A normal Gemini response is no longer mistaken for a safety block.
- The camera is always released after a failed capture, so one failure no longer breaks
  every later capture until a restart.
- `config.json` is written atomically, survives power cuts, handles emoji, and a corrupt file
  is set aside instead of stopping the service.
- `install.sh` now deploys every file the app needs, including the AI provider module and
  its Python environment.
- `update.sh` only updates to a strictly newer release, gives clearer errors, and starts the
  service again if an update fails halfway.
- Deleting photos reports real counts and no longer fails with a server error.

### Changed

- Gemini models: `gemini-3.1-flash-image`, with `gemini-3.1-flash-lite-image` as fallback.
  The preview models used before were retired by Google.
- New portal design with a step tracker (Capture, Brew, Send, Delivered), a first-run
  "Get started" checklist, and settings placed before WiFi.
- The portal works with keyboards and screen readers, and its colors meet WCAG AA contrast.
- Status updates slow down when nothing is happening, pause in hidden tabs, and show a
  "Connection lost" banner when the camera stops answering.
- `install.sh` no longer upgrades the whole system unless you pass `--upgrade-system`.
- The Google GenAI SDK replaces Genkit.

### Added

- A warning before switching to your WiFi, naming the network to rejoin and the address to
  open.
- Show/hide buttons on secret fields.
- Continuous integration: tests on Python 3.11 and 3.13, `shellcheck`, and `visudo`.
- Portal screenshots, `CHANGELOG.md` and `SECURITY.md`.

## [2.0.2] - 2026-06-05

### Added

- AI provider abstraction, so other providers can be added later.
- Python virtual environment support.

## [2.0.1] - 2026-05-21

### Added

- Automatic retry of pending photos.

## [2.0.0] - 2026-05-21

### Added

- Camera module selection (Camera Module 3 or v2.1).
- `update.sh` to update from GitHub releases.

## [1.0.0] - 2026-03-07

First release.

[Unreleased]: https://github.com/nikolmedo/PixelPotion/compare/3.2.0...HEAD
[3.2.0]: https://github.com/nikolmedo/PixelPotion/compare/3.1.0...3.2.0
[3.1.0]: https://github.com/nikolmedo/PixelPotion/compare/3.0.0...3.1.0
[3.0.0]: https://github.com/nikolmedo/PixelPotion/compare/2.0.2...3.0.0
[2.0.2]: https://github.com/nikolmedo/PixelPotion/compare/2.0.1...2.0.2
[2.0.1]: https://github.com/nikolmedo/PixelPotion/compare/2.0.0...2.0.1
[2.0.0]: https://github.com/nikolmedo/PixelPotion/compare/1.0.0...2.0.0
[1.0.0]: https://github.com/nikolmedo/PixelPotion/releases/tag/1.0.0
