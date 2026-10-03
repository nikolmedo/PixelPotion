// Settings page: secret toggles, the two-step WiFi submit and the WiFi scan.
(function () {
    'use strict';

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
        const item = document.createElement('p');
        item.className = 'wifi-message';
        item.textContent = text;
        list.replaceChildren(item);
    }

    function scanWifi() {
        const list = document.getElementById('wifiList');
        list.hidden = false;
        showWifiMessage(list, 'Scanning...');
        fetch('/scan_wifi').then(r => r.json()).then(networks => {
            if (!networks.length) {
                showWifiMessage(list, 'No networks found');
                return;
            }
            const items = networks.map(ssid => {
                const item = document.createElement('button');
                item.type = 'button';
                item.className = 'wifi-item';
                item.textContent = ssid;
                item.addEventListener('click', () => {
                    const field = document.getElementById('wifiSsid');
                    field.value = ssid;
                    field.dispatchEvent(new Event('input'));
                    document.getElementById('wifiPass').focus();
                });
                return item;
            });
            list.replaceChildren(...items);
        }).catch(() => showWifiMessage(list, 'Scan failed'));
    }
    document.getElementById('scanWifiBtn').addEventListener('click', scanWifi);
})();
