// Capture page: style selection, capture, WiFi scan and status polling.
(function () {
    'use strict';

    const pills = document.getElementById('stylePills');
    const captureCard = document.getElementById('captureCard');
    const captureBtn = document.getElementById('captureBtn');
    let selectedStyle = pills ? pills.dataset.activeStyle : '';
    // The button is only busy while the camera is capturing; processing runs
    // in the background and never blocks a new capture.
    let isCapturing = captureCard.dataset.capturing === 'true';

    function selectStyle(id) {
        selectedStyle = id;
        document.querySelectorAll('.style-pill').forEach(p => {
            p.classList.toggle('active', p.dataset.id === id);
        });
        // Persist for the GPIO button.
        PP.post('/set_active_style', {style_id: id})
            .catch(() => PP.toast('Could not save style — reload the page'));
    }

    document.querySelectorAll('.style-pill').forEach(pill => {
        pill.addEventListener('click', () => selectStyle(pill.dataset.id));
    });

    function doCapture() {
        if (isCapturing) return;
        captureBtn.disabled = true;
        isCapturing = true;
        PP.toast('Capturing...');

        const formData = new FormData();
        formData.append('style_id', selectedStyle);
        PP.post('/capture', formData)
            .then(r => r.json())
            .then(data => PP.toast(data.ok ? data.message : data.error))
            .catch(() => PP.toast('Connection error'))
            .finally(() => {
                captureBtn.disabled = false;
                isCapturing = false;
            });
    }
    captureBtn.addEventListener('click', doCapture);

    // Show/hide reveals only what the user typed: stored secrets are never
    // sent to the page.
    document.querySelectorAll('.toggle-secret').forEach(btn => {
        btn.addEventListener('click', () => {
            const input = document.getElementById(btn.dataset.target);
            const reveal = input.type === 'password';
            input.type = reveal ? 'text' : 'password';
            btn.setAttribute('aria-pressed', String(reveal));
            btn.setAttribute('aria-label', (reveal ? 'Hide ' : 'Show ') + btn.dataset.label);
        });
    });

    // Joining a WiFi network ends the setup access point, so the first
    // submit only explains where to reconnect; the second one connects.
    const wifiForm = document.getElementById('wifiForm');
    const wifiNotice = document.getElementById('wifiNotice');
    const wifiSsid = document.getElementById('wifiSsid');
    wifiForm.addEventListener('submit', e => {
        if (!wifiNotice.hidden) return;
        e.preventDefault();
        document.getElementById('wifiNoticeSsid').textContent =
            wifiSsid.value.trim() || 'your WiFi network';
        wifiNotice.hidden = false;
        document.getElementById('wifiSubmit').textContent = 'Got it, connect now';
    });
    wifiSsid.addEventListener('input', () => {
        if (wifiNotice.hidden) return;
        document.getElementById('wifiNoticeSsid').textContent =
            wifiSsid.value.trim() || 'your WiFi network';
    });

    // SSIDs are broadcast by anyone nearby: only ever insert them as text.
    function showWifiMessage(list, text) {
        const item = document.createElement('div');
        item.className = 'wifi-item';
        item.textContent = text;
        list.replaceChildren(item);
    }

    function scanWifi() {
        const list = document.getElementById('wifiList');
        list.style.display = 'block';
        showWifiMessage(list, 'Scanning...');
        fetch('/scan_wifi').then(r => r.json()).then(networks => {
            if (!networks.length) {
                showWifiMessage(list, 'No networks found');
                return;
            }
            const items = networks.map(ssid => {
                const item = document.createElement('div');
                item.className = 'wifi-item';
                item.textContent = ssid;
                item.addEventListener('click', () => {
                    document.getElementById('wifiSsid').value = ssid;
                });
                return item;
            });
            list.replaceChildren(...items);
        }).catch(() => showWifiMessage(list, 'Scan failed'));
    }
    document.getElementById('scanWifiBtn').addEventListener('click', scanWifi);

    // Poll status: the button follows the camera, the text follows processing.
    setInterval(() => {
        fetch('/status_api').then(r => r.json()).then(data => {
            const el = document.getElementById('statusText');
            el.textContent = data.last_action;
            el.classList.toggle('processing', data.processing || data.capturing);
            captureBtn.disabled = data.capturing;
            isCapturing = data.capturing;
        });
    }, 2000);
})();
