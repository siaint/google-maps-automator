let scrapedItems = [];
let logPollTimer = null;

function showScraperStatus(text, type = 'info') {
    const box = document.getElementById('scraperStatus');
    box.className = `status-message ${type}`;
    box.innerText = text;
    box.style.display = 'block';
}

async function loadScrapedData() {
    const res = await fetch('/api/scraped');
    scrapedItems = await res.json();
    renderScrapedTable(scrapedItems);
}

function renderScrapedTable(items) {
    scrapedItems = items;
    const tbody = document.getElementById('scraperTableBody');
    const previewBlock = document.getElementById('previewBlock');
    const counter = document.getElementById('previewCount');
    
    tbody.innerHTML = '';
    counter.innerText = items.length;

    if (items.length === 0) {
        previewBlock.style.display = 'none';
        return;
    }

    previewBlock.style.display = 'block';

    items.forEach((item, idx) => {
        const tr = document.createElement('tr');
        const itemName = (item.name || '').replace(/'/g, "\\'");
        
        let nameHtml = `<strong>${item.name}</strong>`;
        let subParts = [];

        if (item.original_name) {
            subParts.push(`<span>${item.original_name}</span>`);
        }
        if (item.address && item.address.trim() !== '') {
            subParts.push(`<span class="addr-part" title="${item.address}">📍 ${item.address}</span>`);
        }

        if (subParts.length > 0) {
            nameHtml += `<div class="item-sub-info">${subParts.join('<span class="sep">·</span>')}</div>`;
        }

        if (item.url) {
            nameHtml += ` <a href="${item.url}" target="_blank" title="Открыть на Trip.com" style="font-size: 12px; color: #2d88ff; text-decoration: none; margin-left: 6px;">🔗 [Trip]</a>`;
        }

        tr.innerHTML = `
            <td class="checkbox-cell">
                <input type="checkbox" class="scraper-item-check" data-id="${item.id}" checked>
            </td>
            <td>${idx + 1}</td>
            <td>${nameHtml}</td>
            <td>${item.list_name}</td>
            <td class="col-delete">
                <button class="btn-delete" onclick="deleteScraped(${item.id}, '${itemName}')" title="Удалить">
                    <svg viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

async function deleteScraped(id, name) {
    await fetch(`/api/scraped/${id}`, { method: 'DELETE' });
    await loadScrapedData();
}

function toggleAllScraperCheckboxes(checked) {
    document.querySelectorAll('.scraper-item-check').forEach(cb => cb.checked = checked);
    const master = document.getElementById('selectAllScraper');
    if (master) master.checked = checked;
}

async function transferSelectedToEvaluated() {
    const checkboxes = document.querySelectorAll('.scraper-item-check:checked');
    const selectedIds = Array.from(checkboxes).map(cb => parseInt(cb.getAttribute('data-id')));
    const selectedItems = scrapedItems.filter(x => selectedIds.includes(x.id));

    if (selectedItems.length === 0) {
        showModal({ title: "Внимание", body: "Не выбрано ни одного объекта!", confirmText: "Понятно", onConfirm: () => {} });
        return;
    }

    // Спрашиваем пользователя, нужно ли предварительно очистить список на второй вкладке
    showModal({
        title: "Перенос на оценку",
        body: "Очистить текущий список на вкладке оценки перед переносом новых объектов?",
        confirmText: "Да, очистить",
        onConfirm: async () => {
            await executeTransfer(selectedItems, true);
        }
    });

    // Добавим кнопку "Нет, добавить к текущему" в модальное окно через кастомизацию или отменим по отмене. 
    // Поскольку стандартная модалка имеет кнопку "Отмена", сделаем ее поведением "Добавить к текущему".
    const cancelBtn = document.querySelector('.btn-modal-cancel');
    if (cancelBtn) {
        cancelBtn.innerText = "Добавить к текущему";
        cancelBtn.onclick = async () => {
            closeModal();
            await executeTransfer(selectedItems, false);
        };
    }
}

async function executeTransfer(selectedItems, clearExisting) {
    const res = await fetch('/api/scraped/transfer_to_evaluated', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: selectedItems, clear: clearExisting })
    });
    const data = await res.json();
    if (res.ok) {
        showScraperStatus(`✅ Перенесено: выбрано ${selectedItems.length} общ. (новых: ${data.added_count}, дублей: ${data.duplicate_count}, всего на экране: ${data.total_count}). Перейдите на вкладку №2.`, "success");
    }
}

async function runTripScraper() {
    const region = document.getElementById('tripRegion').value.trim();
    let limit = document.getElementById('tripLimit').value.trim();
    const listName = document.getElementById('tripListName').value.trim();
    const runBtn = document.getElementById('btnRunScraper');
    const stopBtn = document.getElementById('btnStopScraper');
    const logBox = document.getElementById('scraperLog');

    if (!region) {
        showScraperStatus("⚠️ Пожалуйста, укажите регион для поиска!", "error");
        document.getElementById('tripRegion').focus();
        return;
    }
    if (!listName) {
        showScraperStatus("⚠️ Пожалуйста, укажите имя целевого списка для Google Maps!", "error");
        document.getElementById('tripListName').focus();
        return;
    }

    runBtn.disabled = true;
    stopBtn.disabled = false;
    runBtn.innerText = 'Сбор выполняется...';
    logBox.style.display = 'block';
    logBox.innerText = '';
    showScraperStatus("Идет сбор данных...", "info");

    if (logPollTimer) clearInterval(logPollTimer);
    logPollTimer = setInterval(async () => {
        try {
            const res = await fetch('/api/scraper/logs');
            const data = await res.json();
            if (data.logs) { logBox.innerText = data.logs; logBox.scrollTop = logBox.scrollHeight; }
        } catch (e) {}
    }, 400);

    try {
        const formData = new URLSearchParams();
        formData.append('region', region);
        formData.append('limit', limit);
        formData.append('list_name', listName);

        const res = await fetch('/api/scrape/trip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: formData
        });
        const data = await res.json();
        
        if (res.ok) {
            showScraperStatus(`✨ Сбор завершен! Найдено: ${data.count} объектов.`, "success");
            loadScrapedData();
        } else {
            showScraperStatus("Ошибка: " + data.message, "error");
        }
    } catch (e) {
        showScraperStatus("Ошибка запроса.", "error");
    } finally {
        if (logPollTimer) { clearInterval(logPollTimer); logPollTimer = null; }
        runBtn.disabled = false;
        stopBtn.disabled = true;
        runBtn.innerText = 'Запустить сбор';
    }
}

async function stopTripScraper() {
    try { await fetch('/api/scrape/trip/stop', { method: 'POST' }); } catch (e) {}
}