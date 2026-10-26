import json
import sys
import io
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from config import PLACES_FILE, SCRAPED_FILE, DB_FILE
from maps_automator import MapsAutomator
from scrapers.trip_scraper import scrape_trip_attractions
import ai_service

automator = MapsAutomator()
executor = ThreadPoolExecutor(max_workers=3)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

trip_cancelled = False


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Вычисляет расстояние между двумя гео-точками в километрах."""
    if not lat1 or not lon1 or not lat2 or not lon2:
        return 0.0
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Проверяем, существует ли старая таблица
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='evaluated_items'")
    table_exists = cursor.fetchone()
    
    if table_exists:
        cursor.execute("PRAGMA table_info(evaluated_items)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if "hidden" not in columns or "address" not in columns:
            cursor.execute("ALTER TABLE evaluated_items RENAME TO evaluated_items_old")
            
            cursor.execute('''
                CREATE TABLE evaluated_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    original_name TEXT,
                    search_region TEXT,
                    url TEXT,
                    image TEXT,
                    lat REAL DEFAULT 0.0,
                    lon REAL DEFAULT 0.0,
                    address TEXT DEFAULT '',
                    rating TEXT DEFAULT '-',
                    personal_rating REAL DEFAULT 0.0,
                    global_rating REAL DEFAULT 0.0,
                    rating_comment TEXT DEFAULT '',
                    list_name TEXT DEFAULT 'Хочу посетить',
                    imported_list TEXT DEFAULT '',
                    hidden INTEGER DEFAULT 0
                )
            ''')
            
            cursor.execute('''
                INSERT INTO evaluated_items (id, name, original_name, search_region, url, image, rating, personal_rating, global_rating, rating_comment, list_name, imported_list)
                SELECT id, name, original_name, search_region, url, image, rating, personal_rating, global_rating, rating_comment, list_name, imported_list 
                FROM evaluated_items_old
            ''')
            cursor.execute("DROP TABLE evaluated_items_old")
    else:
        cursor.execute('''
            CREATE TABLE evaluated_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                original_name TEXT,
                search_region TEXT,
                url TEXT,
                image TEXT,
                lat REAL DEFAULT 0.0,
                lon REAL DEFAULT 0.0,
                address TEXT DEFAULT '',
                rating TEXT DEFAULT '-',
                personal_rating REAL DEFAULT 0.0,
                global_rating REAL DEFAULT 0.0,
                rating_comment TEXT DEFAULT '',
                list_name TEXT DEFAULT 'Хочу посетить',
                imported_list TEXT DEFAULT '',
                hidden INTEGER DEFAULT 0
            )
        ''')

    # Сбрасываем только разрешенные ручные индексы
    cursor.execute("DROP INDEX IF EXISTS idx_evaluated_orig_name")
    cursor.execute("DROP INDEX IF EXISTS idx_evaluated_name_region")
    
    # Создаем чистый уникальный индекс по имени и адресу
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_evaluated_name_address ON evaluated_items (name, address) WHERE address != ''")
        
    conn.commit()
    conn.close()

init_db()

class LogBuffer(io.TextIOBase):
    def __init__(self):
        self.log_lines = []

    def write(self, text):
        if text:
            self.log_lines.append(text)
            if len(self.log_lines) > 500:
                self.log_lines.pop(0)
        sys.__stdout__.write(text)

    def get_logs(self):
        return "".join(self.log_lines)

    def clear(self):
        self.log_lines = []

log_buffer = LogBuffer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    automator.stop()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def load_json_file(file_path: Path) -> list:
    if not file_path.exists():
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            for idx, item in enumerate(data):
                if "id" not in item:
                    item["id"] = idx + 1
            return data
        except Exception:
            return []


def save_json_file(file_path: Path, data: list):
    for idx, item in enumerate(data):
        item["id"] = idx + 1
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


scraped_cache = load_json_file(SCRAPED_FILE)
places_cache = load_json_file(PLACES_FILE)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/scraped")
def get_scraped():
    global scraped_cache
    return JSONResponse(scraped_cache)


@app.post("/api/scrape/trip")
async def scrape_trip(region: str = Form(...), limit: int = Form(300), list_name: str = Form("Хочу посетить")):
    import threading
    global trip_cancelled, scraped_cache

    if trip_cancelled is False and any(t.is_alive() for t in threading.enumerate() if "_run_trip_scraping" in t.name):
        return JSONResponse({"status": "error", "message": "Сбор уже выполняется"}, status_code=400)

    trip_cancelled = False
    log_buffer.clear()

    def is_cancelled():
        return trip_cancelled

    def _run_trip_scraping():
        global scraped_cache
        old_stdout = sys.stdout
        sys.stdout = log_buffer
        try:
            collected_data = scrape_trip_attractions(region, list_name=list_name, limit=limit, stop_callback=is_cancelled)
            scraped_cache = collected_data
            save_json_file(SCRAPED_FILE, scraped_cache)
            print(f"\nВсего успешно обработано: {len(scraped_cache)} объектов.")
        except Exception as e:
            print(f"\n[ERROR] {e}")
        finally:
            sys.stdout = old_stdout

    thread = threading.Thread(target=_run_trip_scraping, name="_run_trip_scraping")
    thread.start()

    return JSONResponse({
        "status": "started",
        "message": "Сбор запущен в фоне"
    })

@app.post("/api/scrape/trip/stop")
async def stop_trip_scrape():
    global trip_cancelled
    trip_cancelled = True
    return JSONResponse({"status": "stopping"})


@app.get("/api/scraper/logs")
def get_scraper_logs():
    return JSONResponse({"logs": log_buffer.get_logs()})


@app.delete("/api/scraped/{item_id}")
def delete_scraped_item(item_id: int):
    global scraped_cache
    scraped_cache = [x for x in scraped_cache if x["id"] != item_id]
    save_json_file(SCRAPED_FILE, scraped_cache)
    return JSONResponse({"status": "deleted", "id": item_id})


@app.delete("/api/scraped")
def clear_scraped():
    global scraped_cache
    scraped_cache = []
    save_json_file(SCRAPED_FILE, scraped_cache)
    return JSONResponse({"status": "cleared"})


@app.post("/api/scraped/transfer_to_evaluated")
async def transfer_to_evaluated(request: Request):
    try:
        body = await request.json()
        if isinstance(body, dict):
            items = body.get("items", [])
            clear_existing = body.get("clear", False)
        else:
            items = body
            clear_existing = False
    except Exception:
        return JSONResponse({"status": "error", "message": "Некорректный JSON"}, status_code=400)

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if clear_existing:
        cursor.execute("UPDATE evaluated_items SET hidden = 1 WHERE hidden = 0")
    
    added_count = 0
    duplicate_count = 0
    
    for item in items:
        raw_name = (item.get("name") or "").strip()
        orig_name = (item.get("original_name") or "").strip()
        item_url = (item.get("url") or "").strip()
        item_img = (item.get("image") or "").strip()
        item_lat = float(item.get("lat") or 0.0)
        item_lon = float(item.get("lon") or 0.0)
        item_addr = (item.get("address") or "").strip()
        search_reg = (item.get("search_region") or "").strip()
        raw_list = (item.get("list_name") or "Хочу посетить").strip()
        
        if not raw_name and not orig_name:
            continue
        
        candidates = []
        if orig_name:
            cursor.execute("SELECT id, search_region, lat, lon, address FROM evaluated_items WHERE LOWER(TRIM(original_name)) = LOWER(?)", (orig_name,))
            candidates = cursor.fetchall()
        
        if not candidates and raw_name:
            cursor.execute("SELECT id, search_region, lat, lon, address FROM evaluated_items WHERE LOWER(TRIM(name)) = LOWER(?)", (raw_name,))
            candidates = cursor.fetchall()

        matched_existing = None
        for cand in candidates:
            c_lat = float(cand["lat"] or 0.0)
            c_lon = float(cand["lon"] or 0.0)

            if item_lat and item_lon and c_lat and c_lon:
                dist = haversine_distance(item_lat, item_lon, c_lat, c_lon)
                if dist <= 15.0:
                    matched_existing = cand
                    break
            else:
                matched_existing = cand
                break
        
        if not matched_existing:
            try:
                cursor.execute('''
                    INSERT INTO evaluated_items (name, original_name, search_region, url, image, lat, lon, address, list_name, hidden)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ''', (raw_name, orig_name, search_reg, item_url, item_img, item_lat, item_lon, item_addr, raw_list))
                added_count += 1
            except sqlite3.IntegrityError:
                duplicate_count += 1
        else:
            existing_regions = [r.strip() for r in (matched_existing["search_region"] or "").split(",") if r.strip()]
            if search_reg and search_reg not in existing_regions:
                existing_regions.append(search_reg)
            merged_regions = ", ".join(existing_regions)

            cursor.execute('''
                UPDATE evaluated_items 
                SET url = COALESCE(NULLIF(?, ''), url), 
                    image = COALESCE(NULLIF(?, ''), image),
                    original_name = COALESCE(NULLIF(?, ''), original_name),
                    lat = CASE WHEN lat = 0.0 THEN ? ELSE lat END,
                    lon = CASE WHEN lon = 0.0 THEN ? ELSE lon END,
                    address = COALESCE(NULLIF(?, ''), address),
                    search_region = ?,
                    hidden = 0
                WHERE id = ?
            ''', (item_url, item_img, orig_name, item_lat, item_lon, item_addr, merged_regions, matched_existing["id"]))
            duplicate_count += 1

    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM evaluated_items WHERE hidden = 0")
    total_count = cursor.fetchone()[0]
    conn.close()
    
    return JSONResponse({
        "status": "success", 
        "added_count": added_count, 
        "duplicate_count": duplicate_count, 
        "total_count": total_count
    })


@app.get("/api/evaluated")
def get_evaluated():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, original_name, search_region, url, image, lat, lon, address,
               rating, personal_rating, global_rating, list_name, imported_list, hidden, rating_comment 
        FROM evaluated_items WHERE hidden = 0 ORDER BY id ASC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    places = load_json_file(PLACES_FILE)
    queued_keys = set()
    for p in places:
        oname = (p.get("original_name") or "").strip().lower()
        rname = (p.get("name") or "").strip().lower()
        if oname: queued_keys.add(oname)
        if rname: queued_keys.add(rname)

    for item in rows:
        oname = (item.get("original_name") or "").strip().lower()
        rname = (item.get("name") or "").strip().lower()
        is_q = False
        if oname and oname in queued_keys: is_q = True
        elif rname and rname in queued_keys: is_q = True
        item["is_queued"] = is_q

    return JSONResponse(rows)


@app.post("/api/evaluated/{item_id}/toggle_hide")
def toggle_hide_evaluated_item(item_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT hidden FROM evaluated_items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if row:
        new_val = 0 if row[0] else 1
        cursor.execute("UPDATE evaluated_items SET hidden = ? WHERE id = ?", (new_val, item_id))
        conn.commit()
        conn.close()
        return JSONResponse({"status": "success", "hidden": new_val})
    conn.close()
    return JSONResponse({"status": "error", "message": "Объект не найден"}, status_code=404)


@app.get("/api/evaluated/{item_id}")
def get_evaluated_item(item_id: int):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM evaluated_items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return JSONResponse({"status": "error", "message": "Объект не найден"}, status_code=404)
    
    item = dict(row)
    places = load_json_file(PLACES_FILE)
    queued_keys = set()
    for p in places:
        oname = (p.get("original_name") or "").strip().lower()
        rname = (p.get("name") or "").strip().lower()
        if oname: queued_keys.add(oname)
        if rname: queued_keys.add(rname)

    oname = (item.get("original_name") or "").strip().lower()
    rname = (item.get("name") or "").strip().lower()
    is_q = False
    if oname and oname in queued_keys: is_q = True
    elif rname and rname in queued_keys: is_q = True
    item["is_queued"] = is_q

    return JSONResponse(item)


@app.post("/api/evaluated/{item_id}/update")
async def update_evaluated_item(item_id: int, request: Request):
    global places_cache
    try:
        body = await request.json()
        list_name = body.get("list_name")
        rating_comment = body.get("rating_comment")
    except Exception:
        return JSONResponse({"status": "error", "message": "Некорректный JSON"}, status_code=400)

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Сначала получаем актуальные данные объекта, включая текущий рейтинг
    cursor.execute("SELECT * FROM evaluated_items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return JSONResponse({"status": "error", "message": "Объект не найден"}, status_code=404)
    
    item = dict(row)
    orig_name = (item.get("original_name") or "").strip().lower()
    r_name = (item.get("name") or "").strip().lower()

    updates = []
    params = []
    if list_name is not None:
        updates.append("list_name = ?")
        params.append(list_name.strip())
        item["list_name"] = list_name.strip()
    if rating_comment is not None:
        updates.append("rating_comment = ?")
        params.append(rating_comment.strip())
        item["rating_comment"] = rating_comment.strip()

    if updates:
        params.append(item_id)
        cursor.execute(f"UPDATE evaluated_items SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    conn.close()

    # Синхронизируем изменения в очереди на третьей вкладке (places.json), если объект там есть
    updated_in_places = False
    for p in places_cache:
        p_oname = (p.get("original_name") or "").strip().lower()
        p_rname = (p.get("name") or "").strip().lower()
        
        match = False
        if orig_name and p_oname and orig_name == p_oname:
            match = True
        elif r_name and p_rname and r_name == p_rname:
            match = True

        if match:
            if list_name is not None:
                p["list_name"] = item["list_name"]
            if rating_comment is not None:
                p["rating_comment"] = item["rating_comment"]
            # Также подтягиваем актуальный рейтинг, если он изменился
            if item.get("rating"):
                p["rating"] = item["rating"]
            updated_in_places = True

    if updated_in_places:
        save_json_file(PLACES_FILE, places_cache)

    return JSONResponse({"status": "success", "id": item_id})


@app.post("/api/places/{item_id}/update")
async def update_place_item(item_id: int, request: Request):
    global places_cache
    try:
        body = await request.json()
        list_name = body.get("list_name")
        search_query = body.get("search_query")
    except Exception:
        return JSONResponse({"status": "error", "message": "Некорректный JSON"}, status_code=400)

    target = next((x for x in places_cache if x["id"] == item_id), None)
    if not target:
        return JSONResponse({"status": "error", "message": "Объект в очереди не найден"}, status_code=404)

    if list_name is not None:
        target["list_name"] = list_name.strip()
    if search_query is not None:
        target["search_query"] = search_query.strip()

    save_json_file(PLACES_FILE, places_cache)
    return JSONResponse({"status": "success", "id": item_id})


@app.post("/api/ai/rate_place")
async def rate_place_endpoint(request: Request):
    import asyncio
    global places_cache
    try:
        body = await request.json()
        item_id = body.get("id")
        place_name = body.get("name", "").strip()
        original_name = body.get("original_name", "").strip()
        address = body.get("address", "").strip()
        full_desc = body.get("full_description", False)
    except Exception:
        return JSONResponse({"status": "error", "message": "Некорректный JSON"}, status_code=400)

    if not place_name and not original_name:
        return JSONResponse({"status": "error", "message": "Не указано имя объекта"}, status_code=400)

    if item_id and not address:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT address FROM evaluated_items WHERE id = ?", (item_id,))
        row = cur.fetchone()
        if row and row[0]:
            address = row[0].strip()
        conn.close()

    loop = asyncio.get_running_loop()
    print(f"\n[AI DEBUG] Отправка в ИИ -> Объект: {place_name}, Адрес: {address}")
    if full_desc:
        res = await loop.run_in_executor(executor, ai_service.rate_attraction, place_name, address, original_name, True, address)
    else:
        res = await loop.run_in_executor(executor, ai_service.quick_audit_attraction, place_name, address, original_name, address)

    # Надежно извлекаем рейтинг из ответа ИИ
    rating_val = res.get("personal_rating")
    if not rating_val or rating_val == 0.0:
        # Пытаемся вытащить из текста комментария, если в поле rating ничего нет
        import re
        comment_text = res.get("comment", "")
        match = re.search(r'\*\*Рейтинг:\s*([\d.]+)\*\*', comment_text, re.IGNORECASE)
        if match:
            try:
                rating_val = float(match.group(1))
            except ValueError:
                pass

    new_rating = str(rating_val if rating_val else "-")
    new_comment = res.get("comment", "")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if item_id:
        cursor.execute('''
            UPDATE evaluated_items 
            SET rating = ?, personal_rating = ?, global_rating = ?, rating_comment = ?
            WHERE id = ?
        ''', (new_rating, res.get("personal_rating", 0.0), res.get("global_rating", 0.0), new_comment, item_id))
    else:
        cursor.execute('''
            UPDATE evaluated_items 
            SET rating = ?, personal_rating = ?, global_rating = ?, rating_comment = ?
            WHERE LOWER(name) = LOWER(?)
        ''', (new_rating, res.get("personal_rating", 0.0), res.get("global_rating", 0.0), new_comment, place_name))
    conn.commit()
    conn.close()

    # Синхронизируем новый рейтинг и комментарий (гайд) с очередью импорта на третьей вкладке
    updated_in_places = False
    for p in places_cache:
        p_oname = (p.get("original_name") or "").strip().lower()
        p_rname = (p.get("name") or "").strip().lower()
        
        match = False
        if original_name and p_oname and original_name.lower() == p_oname:
            match = True
        elif place_name and p_rname and place_name.lower() == p_rname:
            match = True

        if match:
            p["rating"] = new_rating
            p["rating_comment"] = new_comment
            updated_in_places = True

    if updated_in_places:
        save_json_file(PLACES_FILE, places_cache)

    return JSONResponse({"status": "success", "result": res})

@app.post("/api/ai/batch_rate_blitz")
async def batch_rate_blitz(request: Request):
    import asyncio
    try:
        body = await request.json()
        item_ids = body.get("item_ids", [])
    except Exception:
        item_ids = []

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if item_ids:
        placeholders = ','.join(['?'] * len(item_ids))
        cursor.execute(f"SELECT id, name, original_name, search_region, address FROM evaluated_items WHERE id IN ({placeholders}) AND (rating = '-' OR rating IS NULL) AND hidden = 0", item_ids)
    else:
        cursor.execute("SELECT id, name, original_name, search_region, address FROM evaluated_items WHERE (rating = '-' OR rating IS NULL) AND hidden = 0")
        
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    if not rows:
        return JSONResponse({"status": "success", "processed_count": 0, "message": "Нет неоцененных объектов на экране"})

    chunk_size = 15
    processed_count = 0
    loop = asyncio.get_running_loop()

    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        results = await loop.run_in_executor(executor, ai_service.rate_attractions_batch, chunk)
        
        if results and isinstance(results, list):
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            for res in results:
                r_name = res.get("name", "").strip()
                r_score = res.get("rating", 0.0)
                r_reason = res.get("reason", "")
                
                cursor.execute('''
                    UPDATE evaluated_items 
                    SET rating = ?, personal_rating = ?, global_rating = ?, rating_comment = ?
                    WHERE LOWER(name) = LOWER(?) AND hidden = 0
                ''', (str(r_score), float(r_score), float(r_score), f"Блиц-оценка: {r_reason}", r_name))
                processed_count += 1
            conn.commit()
            conn.close()

    return JSONResponse({"status": "success", "processed_count": processed_count})


@app.delete("/api/evaluated/{item_id}")
def delete_evaluated_item(item_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE evaluated_items SET hidden = 1 WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "hidden", "id": item_id})


@app.delete("/api/evaluated")
def clear_evaluated():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE evaluated_items SET hidden = 1 WHERE imported_list != '' AND imported_list IS NOT NULL")
    conn.commit()
    conn.close()
    return JSONResponse({"status": "screen_cleared"})


@app.post("/api/evaluated/transfer_to_places")
async def transfer_to_places(request: Request):
    global places_cache
    try:
        items = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Некорректный JSON"}, status_code=400)

    existing_keys = set()
    for x in places_cache:
        oname = (x.get("original_name") or "").strip().lower()
        rname = (x.get("name") or "").strip().lower()
        if oname:
            existing_keys.add(oname)
        if rname:
            existing_keys.add(rname)
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    added_count = 0
    for item in items:
        raw_name = (item.get("name") or "").strip()
        orig_name = (item.get("original_name") or "").strip()
        item_url = (item.get("url") or "").strip()
        item_img = (item.get("image") or "").strip()
        item_lat = float(item.get("lat") or 0.0)
        item_lon = float(item.get("lon") or 0.0)
        item_addr = (item.get("address") or "").strip()
        search_reg = (item.get("search_region") or "").strip()
        raw_list = (item.get("list_name") or "Хочу посетить").strip()
        guide_text = (item.get("rating_comment") or "").strip()
        
        if not raw_name and not orig_name:
            continue

        q_parts = []
        if orig_name:
            q_parts.append(orig_name)
        if raw_name:
            q_parts.append(raw_name)
        initial_search_query = " ".join(q_parts)

        item_key = f"{orig_name.lower()}_{item_lat:.2f}" if (orig_name and item_lat) else (orig_name.lower() if orig_name else raw_name.lower())
        if item_key not in existing_keys:
            places_cache.append({
                "id": len(places_cache) + 1,
                "name": raw_name,
                "original_name": orig_name,
                "url": item_url,
                "image": item_img,
                "lat": item_lat,
                "lon": item_lon,
                "address": item_addr,
                "search_region": search_reg,
                "rating": item.get("rating", "-"),
                "rating_comment": guide_text,
                "imported_list": "",
                "list_name": raw_list,
                "search_query": initial_search_query,
                "status": "В очереди",
                "error": ""
            })
            existing_keys.add(item_key)
            added_count += 1

    conn.commit()
    conn.close()

    save_json_file(PLACES_FILE, places_cache)
    return JSONResponse({"status": "success", "added_count": added_count, "total_count": len(places_cache)})


@app.get("/api/places")
def get_places(reload: bool = False):
    global places_cache
    if reload:
        places_cache = load_json_file(PLACES_FILE)
    return JSONResponse(places_cache)


def _sync_process(item_id: int):
    global places_cache
    target = next((x for x in places_cache if x["id"] == item_id), None)
    if not target:
        return

    def update_step(step_name: str):
        if automator.is_cancelled:
            return
        target["status"] = step_name
        target["error"] = ""

    list_name = (target.get("list_name") or "Хочу посетить").strip()
    existing_lists = [l.strip() for l in (target.get("imported_list") or "").split(",") if l.strip()]
    if list_name and list_name not in existing_lists:
        existing_lists.append(list_name)
        target["imported_list"] = ", ".join(existing_lists)

    success, message = automator.process_item(target, status_cb=update_step)
    print(f"\n[MAPS DEBUG] Передача в MapsAutomator -> Название: {target.get('name')}, Поисковый запрос: {target.get('search_query')}, Адрес: {target.get('address')}, Координаты: ({target.get('lat')}, {target.get('lon')})")
    
    if success:
        if "уже есть" in message.lower():
            target["status"] = "Уже есть"
        else:
            target["status"] = "Готово"
        target["error"] = ""
    else:
        if automator.is_cancelled or message == "Прервано пользователем":
            target["status"] = "Отменено"
            target["error"] = "Прервано пользователем"
        else:
            target["status"] = "Ошибка"
            target["error"] = message

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        orig_name = target.get("original_name", "").strip()
        r_name = target.get("name", "").strip()
        if orig_name:
            cursor.execute("UPDATE evaluated_items SET imported_list = ? WHERE LOWER(TRIM(original_name)) = LOWER(?)", (target["imported_list"], orig_name))
        elif r_name:
            cursor.execute("UPDATE evaluated_items SET imported_list = ? WHERE LOWER(TRIM(name)) = LOWER(?)", (target["imported_list"], r_name))
        conn.commit()
        conn.close()
    except Exception:
        pass
    
    save_json_file(PLACES_FILE, places_cache)


@app.post("/api/process/{item_id}")
async def process_one(item_id: int):
    import asyncio
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(executor, _sync_process, item_id)

    target = next((x for x in places_cache if x["id"] == item_id), None)
    if not target:
        return JSONResponse({"status": "not_found", "id": item_id})
    return JSONResponse({"status": "success" if target.get("status") in ["Готово", "Уже есть"] else "failed", "message": target.get("error", ""), "id": item_id})


@app.post("/api/stop")
async def stop_process():
    global places_cache
    automator.cancel()
    for item in places_cache:
        if item.get("status") not in ["В очереди", "Готово", "Уже есть", "Ошибка", "Отменено"]:
            item["status"] = "Отменено"
            item["error"] = "Прервано пользователем"
    save_json_file(PLACES_FILE, places_cache)
    return JSONResponse({"status": "stopped"})


@app.delete("/api/places/{item_id}")
def delete_place(item_id: int):
    global places_cache
    places_cache = [x for x in places_cache if x["id"] != item_id]
    save_json_file(PLACES_FILE, places_cache)
    return JSONResponse({"status": "deleted", "id": item_id})


@app.delete("/api/places")
def clear_all_places():
    global places_cache
    places_cache = [x for x in places_cache if x.get("status") in ["В очереди", "Ошибка"]]
    save_json_file(PLACES_FILE, places_cache)
    return JSONResponse({"status": "cleared_completed"})


@app.post("/api/evaluated/{item_id}/reject")
def reject_evaluated_item(item_id: int):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT rating_comment FROM evaluated_items WHERE id = ?", (item_id,))
    row = cursor.fetchone()
    
    if row:
        current_comment = (row["rating_comment"] or "").strip()
        
        if not current_comment.startswith("Отклонено: Не интересно"):
            if current_comment and current_comment != "-":
                new_comment = f"Отклонено: Не интересно\n\n{current_comment}"
            else:
                new_comment = "Отклонено: Не интересно"
                
            cursor.execute("""
                UPDATE evaluated_items 
                SET rating_comment = ?, imported_list = '' 
                WHERE id = ?
            """, (new_comment, item_id))
            conn.commit()
            
    conn.close()
    return JSONResponse({"status": "rejected", "id": item_id})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False, timeout_keep_alive=120)