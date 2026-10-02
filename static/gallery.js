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

    const modal = document.getElementById('modal');
    function openModal(src) {
        document.getElementById('modalImg').src = src;
        modal.classList.add('active');
    }
    function closeModal() {
        modal.classList.remove('active');
    }

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
    document.querySelectorAll('.photo-img').forEach(img => {
        img.addEventListener('click', () => openModal(img.src));
    });
    document.querySelectorAll('.process-one').forEach(btn => {
        btn.addEventListener('click', () => processOne(btn.dataset.filename));
    });
    document.querySelectorAll('.delete-photo-form').forEach(form => {
        form.addEventListener('submit', e => {
            if (!confirm('Delete this photo?')) e.preventDefault();
        });
    });

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

    modal.addEventListener('click', closeModal);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
})();
