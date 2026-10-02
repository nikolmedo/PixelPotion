// Styles page: expand/collapse, edit mode, activate and delete confirmation.
// Style ids and names are read from data attributes, never from inline JS.
(function () {
    'use strict';

    document.querySelectorAll('.style-card').forEach(card => {
        const id = card.dataset.styleId;
        const body = card.querySelector('.style-body');
        const view = card.querySelector('.style-view');
        const edit = card.querySelector('.style-edit');

        const header = card.querySelector('.style-header');
        header.addEventListener('click', () => {
            const open = header.getAttribute('aria-expanded') !== 'true';
            header.setAttribute('aria-expanded', String(open));
            body.hidden = !open;
        });
        card.querySelector('.edit-style').addEventListener('click', () => {
            view.hidden = true;
            edit.hidden = false;
            edit.querySelector('input[name="style_name"]').focus();
        });
        card.querySelector('.cancel-edit').addEventListener('click', () => {
            view.hidden = false;
            edit.hidden = true;
            card.querySelector('.edit-style').focus();
        });

        const activate = card.querySelector('.activate-style');
        if (activate) {
            activate.addEventListener('click', () => {
                PP.post('/set_active_style', {style_id: id})
                    .then(() => window.location.reload())
                    .catch(() => PP.toast('Could not save style — reload the page and try again'));
            });
        }
    });

    document.querySelectorAll('.delete-style-form').forEach(form => {
        form.addEventListener('submit', e => {
            if (!confirm(`Delete style '${form.dataset.styleName}'?`)) e.preventDefault();
        });
    });
})();
