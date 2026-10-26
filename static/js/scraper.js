let currentScrapedItems = [];

async function loadScrapedData() {
    try {
        let res = await fetch('/api/scraped');
        let data = await res.json();
        currentScrapedItems = data;
        renderScrapedTable(data);
    } catch (e) {
        console.error("Ошибка загрузки собранных объектов:", e);
    }
}

function renderScrapedTable(items) {
    let tbody = document.getElementById("scraperTableBody");
    let previewBlock = document.getElementById("previewBlock");
    let previewCount = document.getElementById("previewCount");

    if (!items || items.length === 0) {
        previewBlock.style.display = "none";
        return;
    }

    previewBlock.style.display = "block";
    previewCount.textContent = items.length;
    tbody.innerHTML = "";

    items.forEach((item, index) => {
        let tr = document.createElement("tr");

        let nameHtml = `<div style="font-weight: 600; color: #fff;">${escapeHtml(item.name || '')}</div>`;
        let subParts = [];
        if (item.original_name) {
            subParts.push(`<span style="color: #9ca3af;">${escapeHtml(item.original_name)}</span>`);
        }
        if (item.address) {
            subParts.push(`<span class="addr-part">📍 ${escapeHtml(item.address)}</span>`);
        }
        if (item.url) {
            subParts.push(`<a href="${item.url}" target="_blank" style="color: #3b82f6; text-decoration: none;">🔗 [Trip]</a>`);
        }
        if (subParts.length > 0) {
            nameHtml += `<div class="item-sub-info">${subParts.join('<span class="sep">·</span>')}</div>`;
        }

        tr.innerHTML = `
            <td style="text-align: center; color: #9ca3af; font-weight: 500;">${index + 1}</td>
            <td>${nameHtml}</td>
            <td>
                <input type="text" value="${escapeHtml(item.list_name || 'Хочу посетить')}" 
                    class="edit-input-field" style="width: 100%; font-size: 13px;"
                    onchange="updateScrapedListItem(${item.id}, this.value)">
            </td>
            <td class="col-delete">
                <button class="btn-delete" onclick="deleteScrapedItem(${item.id})" title="Удалить">
                    <svg viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

async function updateScrapedListItem(id, val) {
    let item = currentScrapedItems.find(x => x.id === id);
    if (item) {
        item.list_name = val.trim();
    }
}

async function transferAllToEvaluated() {
    if (!currentScrapedItems || currentScrapedItems.length === 0) {
        alert("Нет объектов для переноса.");
        return;
    }

    let btn = document.getElementById("btnTransferToEval");
    btn.disabled = true;
    btn.textContent = "Перенос...";

    try {
        let res = await fetch('/api/scraped/transfer_to_evaluated', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ items: currentScrapedItems, clear: true })
        });
        let data = await res.json();
        if (data.status === "success") {
            switchTab('evaluateTab', document.getElementById('tabBtnEvaluate'));
            loadEvaluatedData();
        } else {
            alert("Ошибка переноса: " + (data.message || "Неизвестная ошибка"));
        }
    } catch (e) {
        alert("Ошибка сети при переносе.");
    } finally {
        btn.disabled = false;
        btn.textContent = "⚡ Перенести всё на оценку";
    }
}

async function deleteScrapedItem(id) {
    try {
        await fetch(`/api/scraped/${id}`, { method: 'DELETE' });
        currentScrapedItems = currentScrapedItems.filter(x => x.id !== id);
        renderScrapedTable(currentScrapedItems);
    } catch (e) {
        console.error("Ошибка удаления:", e);
    }
}

async function runTripScraper() {
    let region = document.getElementById("tripRegion").value.trim();
    let limit = document.getElementById("tripLimit").value;
    let listName = document.getElementById("tripListName").value.trim() || "Хочу посетить";

    if (!region) {
        alert("Укажите регион для сбора!");
        return;
    }

    let btnRun = document.getElementById("btnRunScraper");
    let btnStop = document.getElementById("btnStopScraper");
    let statusDiv = document.getElementById("scraperStatus");
    let logDiv = document.getElementById("scraperLog");

    btnRun.disabled = true;
    btnStop.disabled = false;
    statusDiv.style.display = "block";
    statusDiv.className = "status-message info";
    statusDiv.textContent = "Сбор запущен в фоновом режиме... Пожалуйста, подождите.";
    logDiv.style.display = "block";
    logDiv.textContent = "Инициализация...";

    try {
        let formData = new FormData();
        formData.append("region", region);
        formData.append("limit", limit);
        formData.append("list_name", listName);

        let res = await fetch('/api/scrape/trip', {
            method: 'POST',
            body: formData
        });
        let data = await res.json();

        if (data.status !== "started" && data.status !== "success") {
            statusDiv.className = "status-message error";
            statusDiv.textContent = "Ошибка: " + (data.message || "Не удалось запустить сбор");
            btnRun.disabled = false;
            btnStop.disabled = true;
            return;
        }

        // Опрашиваем логи и проверяем, когда поток завершит работу
        let checkInterval = setInterval(async () => {
            try {
                // Тянем свежие логи
                let logRes = await fetch('/api/scraper/logs');
                let logData = await logRes.json();
                logDiv.textContent = logData.logs;
                logDiv.scrollTop = logDiv.scrollHeight;

                // Проверяем, завершился ли фоновый поток (ищем строку завершения в логах или проверяем данные)
                if (logData.logs.includes("Всего успешно обработано") || logData.logs.includes("[ERROR]")) {
                    clearInterval(checkInterval);
                    
                    // Загружаем готовые собранные данные
                    await loadScrapedData();
                    
                    btnRun.disabled = false;
                    btnStop.disabled = true;

                    if (logData.logs.includes("[ERROR]")) {
                        statusDiv.className = "status-message error";
                        statusDiv.textContent = "Сбор завершился с ошибкой (см. лог выше).";
                    } else {
                        statusDiv.className = "status-message success";
                        statusDiv.textContent = `Сбор успешно завершен! Объектов в таблице: ${currentScrapedItems.length}`;
                    }
                }
            } catch (e) {}
        }, 1500);

    } catch (e) {
        statusDiv.className = "status-message error";
        statusDiv.textContent = "Ошибка сети при запросе сбора.";
        btnRun.disabled = false;
        btnStop.disabled = true;
    }
}

async function stopTripScraper() {
    await fetch('/api/scrape/trip/stop', { method: 'POST' });
}