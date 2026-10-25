let placesItems = [];
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
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 30px; color: #b0b3b8;">Очередь импорта пуста. Перенесите объекты со вкладки №2!</td></tr>';
        return;
    }

    items.forEach((item, idx) => {
        const tr = document.createElement('tr');
        let badgeClass = 'status-queue';
        if (item.status === 'Ошибка') badgeClass = 'status-error';
        else if (item.status === 'Отменено') badgeClass = 'status-cancelled';
        else if (item.status === 'Готово') badgeClass = 'status-done';
        else if (item.status !== 'В очереди') badgeClass = 'status-processing';

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
            <td>${idx + 1}</td>
            <td>${nameHtml}</td>
            <td>${item.list_name || 'Хочу посетить'}</td>
            <td>
                <span class="status-badge ${badgeClass}" id="status-${item.id}">${item.status}</span>
                <div class="error-desc" id="err-${item.id}">${item.error || ''}</div>
            </td>
            <td class="col-action">
                <button class="btn-single" onclick="processSingle(${item.id})" id="btn-${item.id}">Обработать</button>
            </td>
            <td class="col-delete">
                <button class="btn-delete" onclick="confirmDeletePlace(${item.id}, '${itemName}')" title="Удалить">
                    <svg viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

async function processSingle(id) {
    startStatusPolling();
    const btn = document.getElementById(`btn-${id}`);
    if (btn) btn.disabled = true;
    try { await fetch(`/api/process/${id}`, { method: 'POST' }); }
    finally { stopStatusPolling(); await loadPlacesData(true); }
}

function confirmDeletePlace(id, name) {
    showModal({
        title: "Удаление объекта", body: `Удалить «${name}» из очереди импорта?`, confirmText: "Удалить",
        onConfirm: async () => { await fetch(`/api/places/${id}`, { method: 'DELETE' }); await loadPlacesData(true); }
    });
}

function startStatusPolling() {
    if (!pollTimer) {
        pollTimer = setInterval(async () => {
            try {
                const res = await fetch('/api/places');
                const items = await res.json();
                const remainingCount = items.filter(x => x.status === 'В очереди' || x.status === 'Ошибка').length;
                document.getElementById('counter').innerText = `Осталось в очереди: ${remainingCount}`;

                items.forEach(item => {
                    const statusSpan = document.getElementById(`status-${item.id}`);
                    const errDiv = document.getElementById(`err-${item.id}`);
                    if (statusSpan && item.status !== 'В очереди') {
                        let badgeClass = 'status-queue';
                        if (item.status === 'Ошибка') badgeClass = 'status-error';
                        else if (item.status === 'Отменено') badgeClass = 'status-cancelled';
                        else if (item.status === 'Готово') badgeClass = 'status-done';
                        statusSpan.innerText = item.status;
                        statusSpan.className = `status-badge ${badgeClass}`;
                        if (errDiv) errDiv.innerText = item.error || '';
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
            await new Promise(r => setTimeout(r, 1000));
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