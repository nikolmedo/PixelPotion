# 🧪 PixelPotion

> *Point. Press. Watch the magic happen.*

![alt text](PixelPotion-banner.png)

**PixelPotion** is an AI-powered Raspberry Pi camera that instantly transforms your photos into any artistic style — Pixar 3D, anime, watercolor, oil painting, or anything you can imagine. Press a physical button, and seconds later the original photo plus the styled version land in your Telegram chat. No cloud subscriptions, no apps, no fuss — just pure potion-powered creativity.

Built for makers, photographers, and anyone who wants to add a spark of magic to their memories.

---

## ✨ Features

- **Any art style** — Pixar 3D, anime, watercolor, comic book, cyberpunk, oil painting, and more. If you can describe it, Gemini can paint it.
- **Physical button trigger** — One-press shooting with a GPIO button, no phone needed.
- **Instant Telegram delivery** — Both the original and styled image arrive in your chat automatically.
- **Offline queue** — No WiFi? No problem. Photos are saved and processed the next time you connect.
- **Web configuration portal** — Set up WiFi, API keys, and styles from any browser on your phone or laptop.
- **Custom styles** — Create unlimited styles with your own Gemini prompts directly from the web UI.
- **Standalone hotspot** — Creates its own WiFi access point (`PixelPotion-Setup`) for initial configuration — no router needed.

---

## 📦 Hardware

| Component | Details |
| --- | --- |
| Raspberry Pi Zero 2 W | Main compute board |
| Raspberry Pi Camera Module v2.1 | 8 MP still camera |
| Push button (momentary) | Physical shutter trigger |
| 2x female-to-female jumper wires | Button connections |
| Camera flex cable (Pi Zero) | Included with camera — verify Zero size |
| 5V 2.5A micro-USB power supply | Stable power for the Pi |
| microSD card 16 GB+ | Raspberry Pi OS storage |

---

## 🔌 Hardware Wiring

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

---

## 🛠️ Installation

### Step 1: Flash the SD card

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Select **Raspberry Pi OS Lite (64-bit)** (Bookworm)
3. Open advanced options (⚙️) and configure:
   - Hostname: `pixelpotion`
   - Username: `pi` / set a password
   - Enable SSH: ✅
   - WiFi: set your home network temporarily for installation
4. Flash the image to the microSD card
5. Insert the microSD into the Pi Zero 2 W

### Step 2: First boot and SSH

1. Power on the Pi and wait ~2 minutes
2. Find the Pi's IP on your router or try:

   ```bash
   ping pixelpotion.local
   ```

3. Connect via SSH:

   ```bash
   ssh pi@pixelpotion.local
   ```

### Step 3: Copy project files

From your computer, copy the project to the Pi:

```bash
# Run from the folder containing the project files
scp -r ./* pi@pixelpotion.local:/home/pi/pixelpotion-install/
```

Or clone directly on the Pi if you pushed it to GitHub:

```bash
mkdir -p /home/pi/pixelpotion-install
cd /home/pi/pixelpotion-install
git clone https://github.com/yourusername/pixelpotion .
```

### Step 4: Run the installer

```bash
cd /home/pi/pixelpotion-install
sudo bash install.sh
```

The script will:

- Update system packages
- Install all dependencies (Python, camera, GPIO)
- Configure `hostapd` and `dnsmasq` for the access point
- Install and enable the `pixelpotion` systemd service
- Enable the camera interface

### Step 5: Reboot

```bash
sudo reboot
```

---

## ⚙️ Initial Configuration

### 1. Connect to the PixelPotion access point

After reboot, the Pi creates its own WiFi network:

| Field | Value |
| --- | --- |
| **Network name** | `PixelPotion-Setup` |
| **Password** | `pixelpotion123` |

Connect from your phone or laptop.

### 2. Open the web portal

```text
http://192.168.4.1:8080
```

### 3. Configure your home WiFi

In the **WiFi Settings** section:

1. Click **Scan networks** to list available networks
2. Select your network or type the SSID
3. Enter the password
4. Click **Connect to WiFi**

> After connecting, the Pi stops acting as an access point. Access the portal via its new IP or `http://pixelpotion.local:8080`.

### 4. Get a Google Gemini API Key

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Sign in with your Google account
3. Click **Create API Key**
4. Copy the key (starts with `AIza...`)
5. Paste it into the **Google Gemini API Key** field in the portal

### 5. Configure Telegram

#### Create a bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g. "My PixelPotion")
4. Choose a username (e.g. `my_pixelpotion_bot`)
5. BotFather will give you a **token** like: `123456789:ABCdefGhIjKlMnOpQrStUvWxYz`
6. Paste it into **Telegram Bot Token**

#### Get your Chat ID

1. Search for **@userinfobot** in Telegram
2. Send it any message
3. It replies with your **Chat ID** (a number like `123456789`)
4. Paste it into **Telegram Chat ID**

> **For groups:** Add the bot to the group, then use `@getidsbot` to get the group Chat ID (starts with `-100...`).

1. Click **Save Configuration**

---

## 🎮 Usage

### Physical button

Press the push button. The pipeline runs automatically:

1. 📸 Captures the photo
2. 🧪 Sends it to Gemini AI (Nano Banana) for style transformation
3. 📨 Delivers both photos (original + styled) via Telegram

### Web portal

Click the large 📸 button on the main page. Select a style first using the style pills.

### Offline mode

If there is no WiFi when a photo is captured:

- The photo is saved to the **pending** queue
- Go to the **Gallery** tab when you have WiFi
- Process photos individually or all at once

---

## 🎨 Managing Styles

Open the **Styles** tab in the web portal to:

- Switch the active style
- Create custom styles with your own Gemini prompts
- Edit or delete existing styles

**Prompt tips for best results:**

1. Start with: `TASK: Transform this photograph into...`
2. Ask explicitly for people to remain recognizable
3. Describe indoor and outdoor background handling
4. Include: `No text, watermarks, or logos`
5. End with: `OUTPUT: Generate the transformed image now.`

---

## 📂 Project Structure

### Repository

```text
pixelpotion/
├── app.py                  # Main application
├── constants.py            # AI models, retry count; loads DEFAULT_CONFIG from JSON
├── default_config.json     # Factory defaults: AP credentials, styles, prompts
├── requirements.txt        # Python dependencies
├── install.sh              # Installer script for Raspberry Pi
├── config/
│   ├── hostapd.conf        # Access point configuration
│   ├── dnsmasq.conf        # DHCP/DNS configuration for AP
│   └── pixelpotion.service # systemd service unit
└── templates/
    ├── index.html          # Capture & configuration portal
    ├── styles.html         # Style management
    └── gallery.html        # Pending photos gallery
```

### Installed on the Pi (`/home/pi/pixelpotion/`)

```text
/home/pi/pixelpotion/
├── app.py
├── constants.py
├── default_config.json
├── config.json             # Runtime config (API keys, WiFi) — gitignored, auto-created
├── pixelpotion.log         # Application logs — gitignored
├── requirements.txt
├── templates/
│   ├── index.html
│   ├── styles.html
│   └── gallery.html
└── photos/
    ├── original/           # Raw captured photos
    ├── processed/          # AI-styled output photos
    └── pending/            # Photos waiting for WiFi
```

**Configuration files explained:**

| File | Purpose | In git |
| --- | --- | --- |
| `default_config.json` | Factory defaults — AP name, built-in styles and prompts, GPIO pin | ✅ Yes |
| `config.json` | Runtime state — your API keys, WiFi credentials, active style | ❌ No (gitignored) |

On first run, `config.json` does not exist and all values fall back to `default_config.json`. Once you save anything through the web portal, `config.json` is created and takes precedence. You can safely edit `default_config.json` to change the built-in styles or AP defaults before deploying to a new device.

---

## 🔧 Updating API Keys

### Option 1: Web portal

Open `http://<PI_IP>:8080` and update the fields directly.

### Option 2: Edit config.json via SSH

```bash
ssh pi@pixelpotion.local
nano /home/pi/pixelpotion/config.json
```

Update the relevant fields:

```json
{
  "gemini_api_key": "YOUR_NEW_KEY_HERE",
  "telegram_bot_token": "YOUR_NEW_TOKEN_HERE",
  "telegram_chat_id": "YOUR_NEW_CHAT_ID_HERE"
}
```

Then restart the service:

```bash
sudo systemctl restart pixelpotion
```

---

## 📋 Useful Commands

```bash
# Stream live logs
sudo journalctl -u pixelpotion -f

# Service status
sudo systemctl status pixelpotion

# Restart PixelPotion
sudo systemctl restart pixelpotion

# Stop PixelPotion
sudo systemctl stop pixelpotion

# Test the camera manually
libcamera-still -o test.jpg

# Check current IP
hostname -I
```

---

## 🐛 Troubleshooting

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
- Check service is running: `sudo systemctl status pixelpotion`
