<div align="center">

# 🧪 PixelPotion

> *Point. Press. Watch the magic happen.*

![PixelPotion banner](PixelPotion-banner.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-Zero%202%20W-c51a4a.svg)](https://www.raspberrypi.com/)
[![Powered by Gemini](https://img.shields.io/badge/AI-Google%20Gemini-8E75B2.svg)](https://aistudio.google.com/)

</div>

**PixelPotion** is an AI-powered Raspberry Pi camera that instantly transforms your photos into any artistic style — Pixar 3D, anime, watercolor, oil painting, or anything you can imagine. Press a physical button, and seconds later the original photo plus the styled version land in your Telegram chat. No cloud subscriptions, no apps, no fuss — just pure potion-powered creativity.

Built for makers, photographers, and anyone who wants to add a spark of magic to their memories.

## ⚡ How It Works

1. 📸 **Press the button** — the camera captures your photo
2. 🧪 **The potion brews** — Gemini AI repaints it in your active style
3. 📨 **Magic delivered** — original + styled photo arrive in Telegram

No WiFi at the moment? Photos wait in an offline queue and get processed automatically when you're back online.

## ✨ Features

- **Any art style** — Pixar 3D, anime, watercolor, comic book, cyberpunk... if you can describe it, Gemini can paint it
- **Physical button trigger** — one-press shooting, no phone needed
- **Instant Telegram delivery** — original and styled image, automatically
- **Offline queue with auto-retry** — photos survive WiFi outages and restarts
- **Web configuration portal** — WiFi, API keys, and styles from any browser
- **Custom styles** — unlimited styles with your own prompts, managed from the web UI
- **Standalone hotspot** — creates its own access point (`PixelPotion-Setup`) for initial setup
- **Multi-provider ready** — Gemini by default; the AI backend is provider-agnostic

## 📦 Hardware

| Component | Details |
| --- | --- |
| Raspberry Pi Zero 2 W | Main compute board |
| Pi Camera Module 2.1 (IMX219) or 3 (IMX708) | Still camera |
| Push button (momentary) + 2 jumper wires | Physical shutter trigger |
| Camera flex cable (Pi Zero size) | Verify the Zero connector size |
| 5V 2.5A micro-USB power supply | Stable power |
| microSD card 16 GB+ | Raspberry Pi OS storage |

## 🚀 Quick Start

```bash
# 1. Flash Raspberry Pi OS Lite (64-bit), enable SSH, then on the Pi:
git clone https://github.com/nikolmedo/PixelPotion /home/pi/pixelpotion-install
cd /home/pi/pixelpotion-install
sudo bash install.sh
sudo reboot

# 2. Connect to the "PixelPotion-Setup" WiFi (password: pixelpotion123)
# 3. Open http://192.168.4.1:8080 and configure WiFi, Gemini & Telegram
```

<details>
<summary><b>🔌 Hardware wiring (camera & button)</b></summary>

### Camera

1. Power off the Raspberry Pi
2. Lift the camera connector latch on the Pi Zero 2 W
3. Insert the camera flex cable (contacts facing down)
4. Press the latch closed firmly

> ⚠️ The Pi Zero 2 W uses a **smaller camera connector** than the full-size Pi. Make sure your flex cable is the correct size.

### Button

Connect the push button between these two GPIO pins:

```text
Pin 11 (GPIO17) ←──[ BUTTON ]──→ Pin 9 (GND)

Raspberry Pi Zero 2 W - Pinout:
┌─────────────────────────────────┐
│  (1) 3.3V    (2) 5V             │
│  (3) GPIO2   (4) 5V             │
│  (5) GPIO3   (6) GND            │
│  (7) GPIO4   (8) GPIO14         │
│  (9) GND ◄── (10) GPIO15        │
│ (11) GPIO17◄ (12) GPIO18        │
│  ...                            │
└─────────────────────────────────┘
     ▲
     └── Pin 11 = GPIO17 (button signal)
```

No pull-up resistor needed — the software enables it internally.

</details>

<details>
<summary><b>🛠️ Full installation guide</b></summary>

### Step 1: Flash the SD card

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Select **Raspberry Pi OS Lite (64-bit)** (Bookworm)
3. Open advanced options (⚙️) and configure:
   - Hostname: `pixelpotion`
   - Username: `pi` / set a password
   - Enable SSH: ✅
   - WiFi: set your home network temporarily for installation
4. Flash the image and insert the microSD into the Pi Zero 2 W

### Step 2: First boot and SSH

```bash
ping pixelpotion.local      # find the Pi (wait ~2 min after power-on)
ssh pi@pixelpotion.local
```

### Step 3: Copy project files

```bash
# From your computer:
scp -r ./* pi@pixelpotion.local:/home/pi/pixelpotion-install/

# Or clone directly on the Pi:
mkdir -p /home/pi/pixelpotion-install && cd /home/pi/pixelpotion-install
git clone https://github.com/nikolmedo/PixelPotion .
```

### Step 4: Run the installer and reboot

```bash
cd /home/pi/pixelpotion-install
sudo bash install.sh
sudo reboot
```

The script updates packages, installs all dependencies (Python, camera, GPIO), configures `hostapd`/`dnsmasq` for the access point, and enables the `pixelpotion` systemd service.

</details>

<details>
<summary><b>⚙️ Initial configuration (WiFi, Gemini, Telegram)</b></summary>

### 1. Connect to the PixelPotion access point

| Field | Value |
| --- | --- |
| **Network name** | `PixelPotion-Setup` |
| **Password** | `pixelpotion123` |

### 2. Open the web portal

```text
http://192.168.4.1:8080
```

### 3. Configure your home WiFi

In **WiFi Settings**: scan networks (or type the SSID), enter the password, click **Connect to WiFi**.

> After connecting, the Pi stops acting as an access point. Access the portal via its new IP or `http://pixelpotion.local:8080`.

### 4. Get a Google Gemini API Key

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and sign in
2. Click **Create API Key** and copy the key (starts with `AIza...`)
3. Paste it into the **Google Gemini API Key** field in the portal

### 5. Configure Telegram

**Create a bot:** talk to **@BotFather**, send `/newbot`, choose a name and username. Paste the token it gives you (like `123456789:ABCdefGhIjKlMnOpQrStUvWxYz`) into **Telegram Bot Token**.

**Get your Chat ID:** send any message to **@userinfobot** — it replies with your Chat ID. Paste it into **Telegram Chat ID**.

> **For groups:** add the bot to the group, then use `@getidsbot` to get the group Chat ID (starts with `-100...`).

Click **Save Configuration** — you're ready to brew. 🧪

</details>

## 🎮 Usage

- **Physical button** — press it; capture, styling, and Telegram delivery run automatically
- **Web portal** — pick a style pill and hit the big 📸 button on the main page
- **Offline mode** — photos without WiFi land in the **Gallery** tab; process them individually or all at once when you reconnect

## 🎨 Custom Styles

Open the **Styles** tab to switch the active style, or create your own with custom Gemini prompts.

<details>
<summary><b>Prompt tips for best results</b></summary>

1. Start with: `TASK: Transform this photograph into...`
2. Ask explicitly for people to remain recognizable
3. Describe indoor and outdoor background handling
4. Include: `No text, watermarks, or logos`
5. End with: `OUTPUT: Generate the transformed image now.`

</details>

## 🔄 Maintenance

```bash
sudo bash /home/pi/pixelpotion/update.sh   # update to the latest release
sudo journalctl -u pixelpotion -f          # stream live logs
sudo systemctl restart pixelpotion         # restart the service
```

<details>
<summary><b>Updating API keys & more commands</b></summary>

### Updating API keys

Use the web portal (`http://<PI_IP>:8080`), or edit the runtime config via SSH:

```bash
nano /home/pi/pixelpotion/config.json
sudo systemctl restart pixelpotion
```

### Useful commands

```bash
sudo bash /home/pi/pixelpotion/update.sh --force   # force reinstall latest release
sudo systemctl status pixelpotion                  # service status
sudo systemctl stop pixelpotion                    # stop the service
libcamera-still -o test.jpg                        # test the camera manually
hostname -I                                        # check current IP
```

The update script checks GitHub for a newer release, backs up `config.json`, replaces the code, reinstalls dependencies in the venv, and restarts the service.

</details>

<details>
<summary><b>🐛 Troubleshooting</b></summary>

### "Could not capture photo"

- Verify the camera cable is properly seated
- Test with: `libcamera-still -o test.jpg`
- If it fails, reseat the flex cable and reboot

### "Gemini could not process the image"

- Verify the API key is valid in the portal
- Image generation models may have free-tier rate limits — wait a moment and retry
- Check logs: `sudo journalctl -u pixelpotion -f`

### "Could not send via Telegram"

- Verify the bot token is correct
- Make sure you've sent at least one message to the bot first
- For groups, the bot must be a member

### Can't connect to the access point

- Wait 30–60 seconds after boot
- If the network doesn't appear, reboot the Pi
- Network: `PixelPotion-Setup`, password: `pixelpotion123`

### Web portal won't load

- In AP mode: `http://192.168.4.1:8080`
- In WiFi mode: `http://pixelpotion.local:8080`
- Check the service: `sudo systemctl status pixelpotion`

</details>

## 🧑‍💻 Development

Want to dig into the code, run the test suite, or add a new AI provider?

| Document | What's inside |
| --- | --- |
| [AGENTS.md](AGENTS.md) | Architecture, data flow, design decisions, testing guide, conventions |
| [CLAUDE.md](CLAUDE.md) | Entry point for Claude Code (imports AGENTS.md) |

The test suite runs on any machine — no Raspberry Pi required:

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
```

## 📄 License

[MIT](LICENSE) © Nicolás Olmedo

If PixelPotion brought some magic to your photos, consider [supporting the project](https://github.com/sponsors/nikolmedo) ⭐
