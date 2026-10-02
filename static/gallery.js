// Gallery page: selection, processing, deletion and the photo preview.
// File names come from data attributes and form values, never inline JS.
(function () {
    'use strict';

    function selectedStyle() {
        const sel = document.getElementById('galleryStyle');
        return sel ? sel.value : '';
    }

    function processOne(filename) {
        PP.submitForm('/process_photo', {filename: filename, style_id: selectedStyle()});
    }

    function processAll() {
        if (!confirm('Process all photos with the selected style?')) return;
        PP.submitForm('/process_all', {style_id: selectedStyle()});
    }

    // Native <dialog>: showModal() traps focus and Escape closes it; focus
    // returns to the photo that opened it.
    const preview = document.getElementById('preview');
    const previewImg = document.getElementById('previewImg');
    let opener = null;
    function openPreview(button) {
        const img = button.querySelector('img');
        opener = button;
        previewImg.src = img.src;
        previewImg.alt = button.getAttribute('aria-label').replace(/^Preview /, '');
        // The dialog grows out of the photo that was tapped: start it offset
        // from the screen centre to that photo's centre.
        const box = button.getBoundingClientRect();
        preview.style.setProperty('--from-x', Math.round(box.left + box.width / 2 - window.innerWidth / 2) + 'px');
        preview.style.setProperty('--from-y', Math.round(box.top + box.height / 2 - window.innerHeight / 2) + 'px');
        preview.showModal();
        document.getElementById('previewClose').focus();
    }
    preview.addEventListener('close', () => {
        if (opener) opener.focus();
    });
    // A click on the backdrop lands on the dialog element itself.
    preview.addEventListener('click', e => {
        if (e.target === preview) preview.close();
    });
    document.getElementById('previewClose').addEventListener('click', () => preview.close());

    function photoBoxes() {
        return document.querySelectorAll('input[name="selected_photos"]');
    }
    function syncCard(box) {
        box.closest('.photo-card').classList.toggle('selected', box.checked);
    }
    function toggleSelectAll() {
        const boxes = Array.from(photoBoxes());
        const allChecked = boxes.every(b => b.checked);
        boxes.forEach(b => {
            b.checked = !allChecked;
            syncCard(b);
        });
    }

    photoBoxes().forEach(box => box.addEventListener('change', () => syncCard(box)));
    document.querySelectorAll('.photo-open').forEach(btn => {
        btn.addEventListener('click', () => openPreview(btn));
    });
    document.querySelectorAll('.process-one').forEach(btn => {
        btn.addEventListener('click', () => processOne(btn.dataset.filename));
    });
    document.querySelectorAll('.delete-photo-form').forEach(form => {
        form.addEventListener('submit', e => {
            if (!confirm('Delete this photo?')) e.preventDefault();
        });
    });

    // Refresh the list when photos arrive or leave the queue, unless the
    // user is in the middle of selecting photos or looking at one.
    const grid = document.getElementById('photoGrid');
    const shownCount = grid ? Number(grid.dataset.count) : 0;
    PP.pollStatus(data => {
        const busy = preview.open || Array.from(photoBoxes()).some(b => b.checked);
        if (data.pending_count !== shownCount && !busy) window.location.reload();
    }, () => 10000);

    const bulkForm = document.getElementById('bulkForm');
    if (bulkForm) {
        bulkForm.addEventListener('submit', e => {
            const selected = Array.from(photoBoxes()).filter(b => b.checked).length;
            if (!selected) {
                e.preventDefault();
                PP.toast('Select at least one photo to delete.');
            } else if (!confirm(`Delete ${selected} selected photo(s)?`)) {
                e.preventDefault();
            }
        });
        document.getElementById('selectAllBtn').addEventListener('click', toggleSelectAll);
        const processAllBtn = document.getElementById('processAllBtn');
        if (processAllBtn) processAllBtn.addEventListener('click', processAll);
    }
})();
