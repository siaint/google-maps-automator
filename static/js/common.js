function showModal({ title, body, confirmText = 'Удалить', confirmClass = 'btn-modal-danger', singleButton = false, onConfirm }) {
    const modal = document.getElementById('confirmModal');
    document.getElementById('modalTitle').innerText = title;
    document.getElementById('modalBody').innerText = body;
    
    const confirmBtn = document.getElementById('modalConfirmBtn');
    confirmBtn.innerText = confirmText;
    
    // Сбрасываем и устанавливаем класс кнопки (danger или success/primary)
    confirmBtn.className = confirmClass;
    
    confirmBtn.onclick = () => { closeModal(); onConfirm(); };

    // Управление видимостью кнопки "Отмена"
    const cancelBtn = modal.querySelector('.btn-modal-cancel');
    if (cancelBtn) {
        cancelBtn.style.display = singleButton ? 'none' : 'inline-block';
    }

    modal.style.display = 'flex';
}

function closeModal() {
    const modal = document.getElementById('confirmModal');
    modal.style.display = 'none';
    
    // Возвращаем видимость кнопки отмены на случай будущих модалок
    const cancelBtn = modal.querySelector('.btn-modal-cancel');
    if (cancelBtn) {
        cancelBtn.style.display = 'inline-block';
    }
}

function switchTab(tabId, btn) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById(tabId).classList.add('active');
    btn.classList.add('active');

    if (tabId === 'scraperTab') loadScrapedData();
    if (tabId === 'evaluateTab') loadEvaluatedData();
    if (tabId === 'importTab') loadPlacesData();
}

function openClearModal(target) {
    let title = "Очистка списка";
    let body = "Очистить текущий список?";
    let endpoint = "";

    if (target === 'scraped') { endpoint = '/api/scraped'; body = "Очистить все собранные объекты?"; }
    else if (target === 'evaluated') { endpoint = '/api/evaluated'; body = "Скрыть с экрана уже перенесенные на карты объекты?"; }
    else if (target === 'places') { endpoint = '/api/places'; body = "Очистить выполненные задачи из очереди?"; }

    showModal({
        title: title, body: body, confirmText: "Очистить", confirmClass: "btn-modal-danger", singleButton: false,
        onConfirm: async () => {
            await fetch(endpoint, { method: 'DELETE' });
            if (target === 'scraped') loadScrapedData();
            if (target === 'evaluated') loadEvaluatedData();
            if (target === 'places') loadPlacesData(true);
        }
    });
}