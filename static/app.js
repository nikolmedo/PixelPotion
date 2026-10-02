// Shared helpers for every portal page. No dependencies, no build step.
// Server data reaches scripts only through the DOM (meta tags, data-
// attributes), and scripts insert it only as text (textContent).
(function () {
    'use strict';

    function csrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : '';
    }

    // POST with the CSRF header. `body` may be FormData or a plain object
    // (sent as JSON). Resolves with the Response; rejects on HTTP errors.
    function post(url, body) {
        const headers = {'X-CSRF-Token': csrfToken()};
        let payload = body;
        if (!(body instanceof FormData)) {
            headers['Content-Type'] = 'application/json';
            payload = JSON.stringify(body || {});
        }
        return fetch(url, {method: 'POST', headers: headers, body: payload})
            .then(r => {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r;
            });
    }

    // Build and submit a regular form POST (full page navigation) with the
    // CSRF field, for routes that answer with a redirect and a flash message.
    function submitForm(action, fields) {
        const form = document.createElement('form');
        form.method = 'post';
        form.action = action;
        const all = Object.assign({csrf_token: csrfToken()}, fields || {});
        Object.keys(all).forEach(name => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = name;
            input.value = all[name];
            form.appendChild(input);
        });
        document.body.appendChild(form);
        form.submit();
    }

    let toastTimer = null;
    function toast(message) {
        const el = document.getElementById('toast');
        if (!el) return;
        el.textContent = message;
        el.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
    }

    window.PP = {csrfToken: csrfToken, post: post, submitForm: submitForm, toast: toast};
})();
