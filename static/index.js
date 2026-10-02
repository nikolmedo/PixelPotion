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

    // A one-shot animation: (re)start it by adding a class, and drop the
    // class once the longest part of it has finished.
    function playOnce(el, className, ms) {
        el.classList.remove(className);
        void el.offsetWidth;
        el.classList.add(className);
        clearTimeout(el._playTimer);
        el._playTimer = setTimeout(() => el.classList.remove(className), ms);
    }

    function selectStyle(id) {
        selectedStyle = id;
        document.querySelectorAll('.style-pill').forEach(p => {
            const chosen = p.dataset.id === id;
            p.setAttribute('aria-pressed', String(chosen));
            if (chosen) playOnce(p, 'stamp', 450);
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
        PP.toast('Taking photo...');

        const formData = new FormData();
        formData.append('style_id', selectedStyle);
        PP.post('/capture', formData)
            .then(r => r.json())
            .then(data => PP.toast(data.ok ? data.message : data.error))
            .catch(() => PP.toast('Connection error'))
            .finally(() => {
                captureBtn.disabled = false;
                isCapturing = false;
                poller.now();
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

    // Progress: the step list (text) and the potion bottle (picture) both
    // follow `step`: Capture -> Brew -> Send -> Delivered.
    const TRACKER_ORDER = ['capturing', 'brewing', 'sending', 'done'];
    const STATE_TEXT = {
        done: 'done', active: 'in progress', failed: 'failed',
        waiting: 'waiting for WiFi', todo: 'not started',
    };
    const tracker = document.getElementById('tracker');
    const potion = document.getElementById('potion');

    function trackerStates(step, failedStep) {
        switch (step) {
            case 'capturing': return ['active', 'todo', 'todo', 'todo'];
            case 'queued': return ['done', 'todo', 'todo', 'todo'];
            case 'waiting_wifi': return ['done', 'waiting', 'todo', 'todo'];
            case 'brewing': return ['done', 'active', 'todo', 'todo'];
            case 'sending': return ['done', 'done', 'active', 'todo'];
            case 'done': return ['done', 'done', 'done', 'done'];
            case 'failed': {
                const at = Math.max(0, TRACKER_ORDER.indexOf(failedStep));
                return TRACKER_ORDER.map((_, i) => i < at ? 'done' : (i === at ? 'failed' : 'todo'));
            }
            default: return ['todo', 'todo', 'todo', 'todo'];
        }
    }

    // The cork pop and the failure wobble answer a change of step, so they
    // play only when the step moves there, never on page load.
    function renderPotion(step, failedStep) {
        const previous = potion.dataset.step;
        potion.dataset.step = step;
        potion.dataset.failedStep = failedStep || '';
        if (step === previous) return;
        if (step === 'done') playOnce(potion, 'just-done', 900);
        if (step === 'failed') playOnce(potion, 'just-failed', 600);
    }

    function renderTracker(step, failedStep) {
        tracker.dataset.step = step;
        renderPotion(step, failedStep);
        const states = trackerStates(step, failedStep);
        tracker.querySelectorAll('li').forEach((li, i) => {
            li.dataset.state = states[i];
            li.querySelector('.tracker-state').textContent = ': ' + STATE_TEXT[states[i]];
            if (states[i] === 'active' || states[i] === 'waiting') li.setAttribute('aria-current', 'step');
            else li.removeAttribute('aria-current');
        });
    }
    renderTracker(tracker.dataset.step, tracker.dataset.failedStep);

    const BUSY_STEPS = ['capturing', 'queued', 'brewing', 'sending'];
    function isBusy(data) {
        return data.capturing || data.processing || BUSY_STEPS.includes(data.step);
    }

    function renderWifi(connected) {
        const chip = document.getElementById('wifiChip');
        chip.querySelector('.dot').className = 'dot ' + (connected ? 'green' : 'red');
        chip.querySelector('.chip-text').textContent = 'WiFi: ' + (connected ? 'Connected' : 'AP Mode');
    }

    // The button follows the camera, the text and tracker follow processing.
    const poller = PP.pollStatus(data => {
        const el = document.getElementById('statusText');
        if (el.textContent.trim() !== data.last_action) el.textContent = data.last_action;
        el.classList.toggle('processing', isBusy(data));
        renderTracker(data.step, data.failed_step);
        renderWifi(data.wifi);
        captureBtn.disabled = data.capturing;
        isCapturing = data.capturing;
    }, data => (data === null || isBusy(data) ? 2000 : 10000));
})();
