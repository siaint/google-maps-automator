let evaluatedItems = [];
let selectedPanelItemId = null;
let currentPanelItemObj = null;
let isBatchRating = false;

async function loadEvaluatedData() {
    const res = await fetch('/api/evaluated');
    evaluatedItems = await res.json();
    renderEvaluatedTable(evaluatedItems);
}

function renderEvaluatedTable(items) {
    const hideRejected = document.getElementById('hideRejectedCheck').checked;
    const hideImported = document.getElementById('hideImportedCheck').checked;

    // Фильтрация элементов на основе чекбоксов сверху
    const displayItems = items.filter(item => {
        const isRejected = item.rating_comment && item.rating_comment.startsWith('Отклонено:');
        const isImported = item.imported_list && item.imported_list.trim() !== '';

        if (hideRejected && isRejected) return false;
        if (hideImported && isImported) return false;
        return true;
    });

    const tbody = document.getElementById('evaluatedTableBody');
    tbody.innerHTML = '';

    if (displayItems.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding: 30px; color: #b0b3b8;">Нет объектов для отображения.</td></tr>';
        document.getElementById('previewPanel').style.display = 'none';
        return;
    }

    displayItems.forEach((item, idx) => {
        const tr = document.createElement('tr');
        tr.id = `eval-row-${item.id}`;
        if (selectedPanelItemId === item.id) {
            tr.classList.add('selected-row');
        }

        const isRejected = item.rating_comment && item.rating_comment.startsWith('Отклонено:');
        const isImported = item.imported_list && item.imported_list.trim() !== '';

        // Автоматическое визуальное выделение вердиктов
        if (isRejected) {
            tr.style.opacity = '0.4';
        } else if (isImported) {
            tr.style.opacity = '0.7';
        }
        
        tr.onclick = (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON' || e.target.tagName === 'A' || e.target.tagName === 'TEXTAREA') return;
            selectEvaluatedItem(item.id);
        };

        const ratingVal = parseFloat(item.rating);
        let ratingBadge = `<span class="rating-none">-</span>`;
        if (!isNaN(ratingVal)) {
            let rClass = 'rating-med';
            if (ratingVal >= 7.0) rClass = 'rating-high';
            else if (ratingVal < 4.0) rClass = 'rating-low';
            ratingBadge = `<span class="rating-badge ${rClass}">★ ${ratingVal.toFixed(1)}</span>`;
        }

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

        let tagsHtml = '';
        if (item.search_region && item.search_region.trim() !== '') {
            const regions = item.search_region.split(',').map(r => r.trim()).filter(Boolean);
            if (regions.length > 1) {
                tagsHtml += `<span class="region-tag duplicate" title="Объект встретился в поисках: ${item.search_region}">🔁 Повтор ×${regions.length}: ${item.search_region}</span> `;
            } else {
                tagsHtml += `<span class="region-tag single" title="Регион поиска">📍 ${item.search_region}</span> `;
            }
        }

        if (isImported) {
            const lists = item.imported_list.split(',').map(l => l.trim()).filter(Boolean);
            const titleText = lists.length > 1 ? `В списках (${lists.length}): ${item.imported_list}` : `В списках: ${item.imported_list}`;
            tagsHtml += `<span class="imported-list-tag" title="Уже добавлен в эти списки Google Maps">📌 ${titleText}</span>`;
        }

        if (tagsHtml) {
            nameHtml += `<div style="margin-top: 2px;">${tagsHtml}</div>`;
        }

        let statusBadges = '';
        if (item.rating_comment && item.rating_comment.trim() !== '') {
            if (isRejected) {
                statusBadges += `<span style="display: inline-block; background: rgba(107, 114, 128, 0.15); color: #9ca3af; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-right: 6px; margin-top: 3px;">❌ Не интересно</span>`;
            } else if (item.rating_comment.startsWith('Блиц-оценка:')) {
                statusBadges += `<span style="display: inline-block; background: rgba(245, 158, 11, 0.15); color: #f59e0b; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-right: 6px; margin-top: 3px;">⚡ Пройдена блиц-оценка</span>`;
            } else if (item.rating_comment.includes('Суть:') && item.rating_comment.includes('Вердикт:')) {
                statusBadges += `<span style="display: inline-block; background: rgba(139, 92, 246, 0.15); color: #a78bfa; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-right: 6px; margin-top: 3px;">⚡ Экспресс-аудит</span>`;
            } else {
                statusBadges += `<span style="display: inline-block; background: rgba(52, 211, 153, 0.15); color: #34d399; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-right: 6px; margin-top: 3px;">📖 Есть подробный гайд</span>`;
            }
        }

        if (statusBadges) {
            nameHtml += `<div style="margin-top: 2px;">${statusBadges}</div>`;
        }

        tr.innerHTML = `
            <td>${idx + 1}</td>
            <td>${nameHtml}</td>
            <td>${ratingBadge}</td>
            <td class="col-delete">
                <button class="btn-delete" onclick="deleteEvaluated(${item.id}, '${itemName}'); event.stopPropagation();" title="Удалить">
                    <svg viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    if (!selectedPanelItemId && displayItems.length > 0) {
        selectEvaluatedItem(displayItems[0].id);
    } else if (selectedPanelItemId) {
        selectEvaluatedItem(selectedPanelItemId);
    }
}

async function selectEvaluatedItem(id) {
    selectedPanelItemId = id;
    toggleEditComment(false);
    toggleEditList(false);

    document.querySelectorAll('#evaluatedTableBody tr').forEach(tr => tr.classList.remove('selected-row'));
    const activeRow = document.getElementById(`eval-row-${id}`);
    if (activeRow) activeRow.classList.add('selected-row');

    const res = await fetch(`/api/evaluated/${id}`);
    if (!res.ok) return;
    const item = await res.json();
    currentPanelItemObj = item;

    const panel = document.getElementById('previewPanel');
    panel.style.display = 'block';
    
    document.getElementById('panelTitle').innerText = item.name + (item.original_name ? ` (${item.original_name})` : '');
    
    const addrDiv = document.getElementById('panelAddress');
    if (item.address && item.address.trim() !== '') {
        addrDiv.innerHTML = `📍 <strong>Адрес:</strong> ${item.address}`;
        addrDiv.style.display = 'block';
    } else {
        addrDiv.style.display = 'none';
    }

    const regionsContainer = document.getElementById('panelRegionsBadge');
    if (item.search_region && item.search_region.trim() !== '') {
        const regions = item.search_region.split(',').map(r => r.trim()).filter(Boolean);
        if (regions.length > 1) {
            regionsContainer.innerHTML = `<div class="region-tag duplicate" style="display:inline-block; margin-bottom: 4px;">🔁 Встретился в ${regions.length} поисках: <strong>${item.search_region}</strong></div>`;
        } else {
            regionsContainer.innerHTML = `<div class="region-tag single" style="display:inline-block; margin-bottom: 4px;">📍 Регион поиска: <strong>${item.search_region}</strong></div>`;
        }
    } else {
        regionsContainer.innerHTML = '';
    }

    const badgeContainer = document.getElementById('panelImportBadge');
    if (item.imported_list && item.imported_list.trim() !== '') {
        badgeContainer.innerHTML = `<div style="background: rgba(59, 130, 246, 0.12); border: 1px solid rgba(59, 130, 246, 0.3); color: #93c5fd; padding: 6px 10px; border-radius: 4px; font-size: 12px;">📌 Добавлен в списки: <strong>${item.imported_list}</strong></div>`;
    } else {
        badgeContainer.innerHTML = '';
    }

    document.getElementById('panelTargetListName').innerText = item.list_name || 'Хочу посетить';
    document.getElementById('panelTargetListInput').value = item.list_name || 'Хочу посетить';

    const imgContainer = document.getElementById('panelImageContainer');
    if (item.image) {
        imgContainer.innerHTML = `<img src="${item.image}" style="max-width: 100%; max-height: 180px; border-radius: 4px; object-fit: cover;" onerror="this.parentElement.innerHTML='<span style=\\'color:#6b7280; font-size:12px;\\'>Не удалось загрузить фото</span>'">`;
    } else {
        imgContainer.innerHTML = `<span style="color: #6b7280; font-size: 12px;">Изображение отсутствует</span>`;
    }

    const linksContainer = document.getElementById('panelLinks');
    let linksHtml = '';
    if (item.url) {
        linksHtml += `<a href="${item.url}" target="_blank" style="color: #2d88ff; text-decoration: none; margin-right: 12px;">🔗 Страница на Trip.com</a>`;
    }
    if (item.lat && item.lon && item.lat !== 0.0) {
        const mapUrl = `https://www.google.com/maps?q=${item.lat},${item.lon}`;
        linksHtml += `<a href="${mapUrl}" target="_blank" style="color: #10b981; text-decoration: none;">🗺 Точка на карте (${item.lat.toFixed(4)}, ${item.lon.toFixed(4)})</a>`;
    }
    linksContainer.innerHTML = linksHtml;

    const ratingVal = parseFloat(item.rating);
    let ratingHtml = `<span style="color: #9ca3af;">Оценка не проводилась</span>`;
    if (!isNaN(ratingVal)) {
        let color = ratingVal >= 7 ? '#4ade80' : ratingVal >= 4 ? '#facc15' : '#f87171';
        ratingHtml = `<strong style="color: ${color};">Рейтинг: ${ratingVal.toFixed(1)}</strong>`;
    }
    document.getElementById('panelRating').innerHTML = ratingHtml;
    
    const commentText = item.rating_comment || '';
    document.getElementById('panelTextContent').innerText = commentText || 'Нажмите «Экспресс-аудит», чтобы оценить перспективность объекта.';
    document.getElementById('panelTextEditArea').value = commentText;
}

function toggleEditComment(showEdit) {
    document.getElementById('panelTextViewContainer').style.display = showEdit ? 'none' : 'block';
    document.getElementById('panelTextEditContainer').style.display = showEdit ? 'block' : 'none';
    document.getElementById('btnEditComment').style.display = showEdit ? 'none' : 'inline-block';
    if (showEdit) {
        const area = document.getElementById('panelTextEditArea');
        area.value = currentPanelItemObj ? (currentPanelItemObj.rating_comment || '') : '';
        area.focus();
    }
}

async function saveCommentEdit() {
    if (!selectedPanelItemId) return;
    const newText = document.getElementById('panelTextEditArea').value;
    
    const res = await fetch(`/api/evaluated/${selectedPanelItemId}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating_comment: newText })
    });
    
    if (res.ok) {
        if (currentPanelItemObj) currentPanelItemObj.rating_comment = newText;
        document.getElementById('panelTextContent').innerText = newText;
        toggleEditComment(false);
        loadEvaluatedData();
    } else {
        alert('Ошибка при сохранении текста');
    }
}

function toggleEditList(showEdit) {
    document.getElementById('targetDisplayRow').style.display = showEdit ? 'none' : 'flex';
    document.getElementById('targetEditRow').style.display = showEdit ? 'block' : 'none';
    if (showEdit) {
        const inp = document.getElementById('panelTargetListInput');
        inp.value = currentPanelItemObj ? (currentPanelItemObj.list_name || 'Хочу посетить') : '';
        inp.focus();
    }
}

async function saveListEdit() {
    if (!selectedPanelItemId) return;
    const newList = document.getElementById('panelTargetListInput').value.trim() || 'Хочу посетить';
    
    const res = await fetch(`/api/evaluated/${selectedPanelItemId}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ list_name: newList })
    });
    
    if (res.ok) {
        if (currentPanelItemObj) currentPanelItemObj.list_name = newList;
        document.getElementById('panelTargetListName').innerText = newList;
        toggleEditList(false);
        loadEvaluatedData();
    } else {
        alert('Ошибка при сохранении списка');
    }
}

async function rateSelectedPanelItem(fullDescription = false) {
    if (!selectedPanelItemId) return;
    toggleEditComment(false);
    
    const resMeta = await fetch(`/api/evaluated/${selectedPanelItemId}`);
    if (!resMeta.ok) return;
    const item = await resMeta.json();

    const textDiv = document.getElementById('panelTextContent');
    textDiv.innerText = fullDescription ? '🤖 Генерация полного описания через Gemini...' : '⚡ Экспресс-аудит объекта (дешево)...';

    try {
        const res = await fetch('/api/ai/rate_place', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                id: item.id,
                name: item.name, 
                original_name: item.original_name || '', 
                address: item.address || '',
                full_description: fullDescription
            })
        });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            selectEvaluatedItem(selectedPanelItemId);
            loadEvaluatedData();
        } else {
            textDiv.innerText = 'Ошибка при генерации.';
        }
    } catch (e) {
        textDiv.innerText = 'Сбой запроса к серверу.';
    }
}

async function rejectSelectedPanelItem() {
    if (!selectedPanelItemId) return;
    try {
        const res = await fetch(`/api/evaluated/${selectedPanelItemId}/reject`, { method: 'POST' });
        if (res.ok) {
            selectEvaluatedItem(selectedPanelItemId);
            loadEvaluatedData();
        } else {
            alert('Ошибка при отклонении объекта.');
        }
    } catch (e) {
        alert('Сбой запроса к серверу.');
    }
}

async function runBlitzRate() {
    const itemIds = evaluatedItems
        .filter(item => {
            const isRejected = item.rating_comment && item.rating_comment.startsWith('Отклонено:');
            const isImported = item.imported_list && item.imported_list.trim() !== '';
            return !isRejected && !isImported && (!item.rating || item.rating === '-');
        })
        .map(item => item.id);

    if (itemIds.length === 0) {
        alert('Нет подходящих неоцененных объектов на экране!');
        return;
    }

    const btn = document.getElementById('btnBlitzRate');
    btn.disabled = true;
    btn.innerText = '⏳ Блиц-оценка выполняется...';

    try {
        const res = await fetch('/api/ai/batch_rate_blitz', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_ids: itemIds })
        });
        const data = await res.json();
        if (res.ok) {
            alert(`✅ Блиц-оценка завершена! Обработано объектов: ${data.processed_count}`);
            loadEvaluatedData();
        } else {
            alert('Ошибка при выполнении блиц-оценки.');
        }
    } catch (e) {
        alert('Сбой запроса к серверу.');
    } finally {
        btn.disabled = false;
        btn.innerText = '⚡ Блиц-оценка (экран)';
    }
}

async function rateAllItems() {
    if (isBatchRating) {
        isBatchRating = false;
        document.getElementById('btnRateAll').innerText = '⚡ Оценить всё через ИИ (экспресс)';
        return;
    }

    isBatchRating = true;
    const btn = document.getElementById('btnRateAll');
    btn.innerText = '■ Остановить';
    btn.className = 'btn-stop';

    const targetItems = evaluatedItems.filter(item => {
        const isRejected = item.rating_comment && item.rating_comment.startsWith('Отклонено:');
        const isImported = item.imported_list && item.imported_list.trim() !== '';
        return !isRejected && !isImported && (!item.rating || item.rating === '-');
    });

    for (let item of targetItems) {
        if (!isBatchRating) break;
        selectedPanelItemId = item.id;
        selectEvaluatedItem(item.id);
        await rateSelectedPanelItem(false);
        await new Promise(r => setTimeout(r, 600));
    }

    isBatchRating = false;
    btn.innerText = '⚡ Оценить всё через ИИ (экспресс)';
    btn.className = 'btn-ai';
}

async function deleteEvaluated(id, name) {
    await fetch(`/api/evaluated/${id}`, { method: 'DELETE' });
    await loadEvaluatedData();
}

async function transferCurrentPanelItem() {
    if (!selectedPanelItemId) return;
    const resMeta = await fetch(`/api/evaluated/${selectedPanelItemId}`);
    if (!resMeta.ok) return;
    const item = await resMeta.json();

    const res = await fetch('/api/evaluated/transfer_to_places', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify([item])
    });
    const data = await res.json();
    if (res.ok) {
        showModal({
            title: "Успешно",
            body: `Объект «${item.name}» успешно передан в очередь импорта карт!`,
            confirmText: "Хорошо",
            confirmClass: "btn-modal-success",
            singleButton: true,
            onConfirm: () => {}
        });
        loadEvaluatedData();
        loadPlacesData(true);
    }
}