let placesItems = [];
let selectedImportId = null;
let currentImportItemObj = null;
let pollTimer = null;
let shouldStop = false;

async function loadPlacesData(reloadFromFile = false) {
    const url = reloadFromFile ? '/api/places?reload=true' : '/api/places';
    const res = await fetch(url);
    placesItems = await res.json();
    renderPlacesTable(placesItems);
}

function renderPlacesTable(items) {
    const remainingCount = items.filter(x => x.status === 'В очереди' || x.status === 'Ошибка').length;
    document.getElementById('counter').innerText = `Осталось в очереди: ${remainingCount}`;
    const tbody = document.getElementById('placesTable');
    tbody.innerHTML = '';

    if (items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 30px; color: #b0b3b8;">Очередь импорта пуста. Перенесите объекты со вкладки №2!</td></tr>';
        document.getElementById('importPreviewPanel').style.display = 'none';
        return;
    }

    items.forEach((item, idx) => {
        const tr = document.createElement('tr');
        tr.id = `import-row-${item.id}`;
        if (selectedImportId === item.id) {
            tr.classList.add('selected-row');
        }

        tr.onclick = (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON' || e.target.tagName === 'A' || e.target.tagName === 'TEXTAREA') return;
            selectImportItem(item.id);
        };

        let badgeClass = 'status-queue';
        let statusText = item.status || 'В очереди';
        if (statusText === 'Ошибка') badgeClass = 'status-error';
        else if (statusText === 'Отменено') badgeClass = 'status-cancelled';
        else if (statusText === 'Готово') badgeClass = 'status-done';
        else if (statusText === 'Уже есть') badgeClass = 'status-done';
        else if (statusText !== 'В очереди') badgeClass = 'status-processing';

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

        const ratingVal = parseFloat(item.rating);
        let ratingBadge = `<span class="rating-none">-</span>`;
        if (!isNaN(ratingVal)) {
            let rClass = 'rating-med';
            if (ratingVal >= 7.0) rClass = 'rating-high';
            else if (ratingVal < 4.0) rClass = 'rating-low';
            ratingBadge = `<span class="rating-badge ${rClass}">★ ${ratingVal.toFixed(1)}</span>`;
        }

        let statusHtml = `<span class="status-badge ${badgeClass}" id="status-${item.id}">${statusText}</span>`;
        if (statusText === 'Ошибка' && item.error) {
            statusHtml += ` <span style="font-size: 11px; color: #f87171; cursor: help; margin-left: 4px;" title="${item.error.replace(/"/g, '&quot;')}">⚠️</span>`;
        }

        tr.innerHTML = `
            <td>${idx + 1}</td>
            <td>${nameHtml}</td>
            <td>${ratingBadge}</td>
            <td>${statusHtml}</td>
            <td class="col-delete">
                <button class="btn-delete" onclick="confirmDeletePlace(${item.id}, '${itemName}'); event.stopPropagation();" title="Удалить из очереди">
                    <svg viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    if (!selectedImportId && items.length > 0) {
        selectImportItem(items[0].id);
    } else if (selectedImportId) {
        selectImportItem(selectedImportId);
    }
}

async function selectImportItem(id) {
    selectedImportId = id;
    toggleEditImportList(false);

    document.querySelectorAll('#placesTable tr').forEach(tr => tr.classList.remove('selected-row'));
    const activeRow = document.getElementById(`import-row-${id}`);
    if (activeRow) activeRow.classList.add('selected-row');

    const target = placesItems.find(x => x.id === id);
    if (!target) return;
    currentImportItemObj = target;

    const panel = document.getElementById('importPreviewPanel');
    panel.style.display = 'block';

    document.getElementById('importPanelTitle').innerText = target.name + (target.original_name ? ` (${target.original_name})` : '');
    
    if (!target.search_query) {
        let qParts = [];
        if (target.original_name) qParts.push(target.original_name);
        if (target.name) qParts.push(target.name);
        target.search_query = qParts.join(' ');
    }
    document.getElementById('importSearchQueryInput').value = target.search_query;

    const addrDiv = document.getElementById('importPanelAddress');
    if (target.address && target.address.trim() !== '') {
        addrDiv.innerHTML = `📍 <strong>Адрес:</strong> ${target.address}`;
        addrDiv.style.display = 'block';
    } else {
        addrDiv.style.display = 'none';
    }

    const historyContainer = document.getElementById('importPanelImportHistory');
    if (target.imported_list && target.imported_list.trim() !== '') {
        const listsArray = target.imported_list.split(',').map(l => l.trim()).filter(Boolean);
        let listsHtml = listsArray.map(l => `<div style="margin-bottom: 2px;">📌 ${l}</div>`).join('');
        historyContainer.innerHTML = listsHtml;
    } else {
        historyContainer.innerHTML = `<span style="color: #9ca3af;">Объект еще не добавлялся ни в один список карт.</span>`;
    }

    const imgContainer = document.getElementById('importPanelImageContainer');
    if (target.image) {
        imgContainer.innerHTML = `<img src="${target.image}" style="max-width: 100%; max-height: 180px; border-radius: 4px; object-fit: cover;" onerror="this.parentElement.innerHTML='<span style=\\'color:#6b7280; font-size:12px;\\'>Не удалось загрузить фото</span>'">`;
    } else {
        imgContainer.innerHTML = `<span style="color: #6b7280; font-size: 12px;">Изображение отсутствует</span>`;
    }

    const linksContainer = document.getElementById('importPanelLinks');
    let linksHtml = '';
    if (target.url) {
        linksHtml += `<a href="${target.url}" target="_blank" style="color: #2d88ff; text-decoration: none; margin-right: 12px;">🔗 Страница на Trip.com</a>`;
    }
    if (target.lat && target.lon && target.lat !== 0.0) {
        const mapUrl = `https://www.google.com/maps?q=${target.lat},${target.lon}`;
        linksHtml += `<a href="${mapUrl}" target="_blank" style="color: #10b981; text-decoration: none;">🗺 Точка на карте</a>`;
    }
    linksContainer.innerHTML = linksHtml;

    document.getElementById('importPanelTargetListName').innerText = target.list_name || 'Хочу посетить';
    document.getElementById('importPanelTargetListInput').value = target.list_name || 'Хочу посетить';

    const textContent = target.rating_comment || 'Описание отсутствует.';
    document.getElementById('importPanelTextContent').innerText = textContent;

    const procBtn = document.getElementById('importPanelProcessBtn');
    if (target.status === 'Выполняется...') {
        procBtn.disabled = true;
        procBtn.innerText = '⏳ Добавление в Maps...';
    } else {
        procBtn.disabled = false;
        procBtn.innerText = '▶ Добавить объект на Google Maps';
    }
}

async function saveImportSearchQuery() {
    if (!selectedImportId) return;
    const newQuery = document.getElementById('importSearchQueryInput').value.trim();
    if (!newQuery) return;

    const res = await fetch(`/api/places/${selectedImportId}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ search_query: newQuery })
    });

    if (res.ok) {
        if (currentImportItemObj) currentImportItemObj.search_query = newQuery;
        loadPlacesData(true);
        const btn = document.querySelector('#importSearchQueryInput + button');
        const oldText = btn.innerText;
        btn.innerText = '✔';
        setTimeout(() => btn.innerText = oldText, 1000);
    } else {
        alert('Ошибка при сохранении поискового запроса');
    }
}

function toggleEditImportList(show) {
    document.getElementById('importTargetDisplayRow').style.display = show ? 'none' : 'flex';
    document.getElementById('importTargetEditRow').style.display = show ? 'block' : 'none';
    if (show) {
        const inp = document.getElementById('importPanelTargetListInput');
        inp.value = currentImportItemObj ? (currentImportItemObj.list_name || 'Хочу посетить') : '';
        inp.focus();
    }
}

async function saveImportListEdit() {
    if (!selectedImportId) return;
    const newList = document.getElementById('importPanelTargetListInput').value.trim() || 'Хочу посетить';

    const res = await fetch(`/api/places/${selectedImportId}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ list_name: newList })
    });

    if (res.ok) {
        if (currentImportItemObj) currentImportItemObj.list_name = newList;
        document.getElementById('importPanelTargetListName').innerText = newList;
        toggleEditImportList(false);
        loadPlacesData(true);
    } else {
        alert('Ошибка при изменении списка');
    }
}

async function processSelectedImportItem() {
    if (!selectedImportId) return;
    startStatusPolling();
    const procBtn = document.getElementById('importPanelProcessBtn');
    procBtn.disabled = true;
    procBtn.innerText = '⏳ Добавление в Maps...';
    try { 
        await fetch(`/api/process/${selectedImportId}`, { method: 'POST' }); 
    } finally { 
        stopStatusPolling(); 
        await loadPlacesData(true); 
    }
}

function confirmDeletePlace(id, name) {
    showModal({
        title: "Удаление объекта", body: `Удалить «${name}» из очереди импорта? (В базе данных на 2-й вкладке он останется)`, confirmText: "Удалить",
        onConfirm: async () => { await fetch(`/api/places/${id}`, { method: 'DELETE' }); await loadPlacesData(true); }
    });
}

function startStatusPolling() {
    if (!pollTimer) {
        pollTimer = setInterval(async () => {
            try {
                const res = await fetch('/api/places');
                const items = await res.json();
                placesItems = items;
                const remainingCount = items.filter(x => x.status === 'В очереди' || x.status === 'Ошибка').length;
                document.getElementById('counter').innerText = `Осталось в очереди: ${remainingCount}`;

                items.forEach(item => {
                    const statusSpan = document.getElementById(`status-${item.id}`);
                    if (statusSpan && item.status !== 'В очереди') {
                        let badgeClass = 'status-queue';
                        let statusText = item.status || 'В очереди';
                        if (statusText === 'Ошибка') badgeClass = 'status-error';
                        else if (statusText === 'Отменено') badgeClass = 'status-cancelled';
                        else if (statusText === 'Готово') badgeClass = 'status-done';
                        else if (statusText === 'Уже есть') badgeClass = 'status-done';
                        statusSpan.innerText = statusText;
                        statusSpan.className = `status-badge ${badgeClass}`;
                    }
                });
            } catch (e) { console.error(e); }
        }, 400);
    }
}

function stopStatusPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function startBatch() {
    shouldStop = false;
    document.getElementById('btnStartAll').disabled = true;
    document.getElementById('btnStop').disabled = false;
    startStatusPolling();

    try {
        while (!shouldStop) {
            const res = await fetch('/api/places');
            const items = await res.json();
            if (shouldStop) break;

            const nextItem = items.find(x => x.status === 'В очереди');
            if (!nextItem) break;

            await fetch(`/api/process/${nextItem.id}`, { method: 'POST' });
            await loadPlacesData(true);
            if (shouldStop) break;
            await new RoomDelay(1000); // Или задержка 1с
        }
    } finally {
        shouldStop = false;
        stopStatusPolling();
        document.getElementById('btnStartAll').disabled = false;
        document.getElementById('btnStop').disabled = true;
        await loadPlacesData(true);
    }
}

async function stopBatch() {
    shouldStop = true;
    await fetch('/api/stop', { method: 'POST' });
    await loadPlacesData(true);
}