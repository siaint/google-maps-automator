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
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS evaluated_items (
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
            archived INTEGER DEFAULT 0,
            hidden INTEGER DEFAULT 0
        )
    ''')

    cursor.execute("PRAGMA table_info(evaluated_items)")
    columns = [col[1] for col in cursor.fetchall()]
    if "archived" not in columns:
        cursor.execute("ALTER TABLE evaluated_items ADD COLUMN archived INTEGER DEFAULT 0")
    if "hidden" not in columns:
        cursor.execute("ALTER TABLE evaluated_items ADD COLUMN hidden INTEGER DEFAULT 0")
    if "lat" not in columns:
        cursor.execute("ALTER TABLE evaluated_items ADD COLUMN lat REAL DEFAULT 0.0")
    if "lon" not in columns:
        cursor.execute("ALTER TABLE evaluated_items ADD COLUMN lon REAL DEFAULT 0.0")
    if "address" not in columns:
        cursor.execute("ALTER TABLE evaluated_items ADD COLUMN address TEXT DEFAULT ''")

    cursor.execute("DROP INDEX IF EXISTS idx_evaluated_orig_name")
        
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
    import asyncio
    global trip_cancelled, scraped_cache

    trip_cancelled = False
    log_buffer.clear()
    collected_data = []

    def is_cancelled():
        return trip_cancelled

    def _run_trip_scraping():
        nonlocal collected_data
        old_stdout = sys.stdout
        sys.stdout = log_buffer
        try:
            collected_data = scrape_trip_attractions(region, list_name=list_name, limit=limit, stop_callback=is_cancelled)
            return True, None
        except Exception as e:
            print(f"\n[ERROR] {e}")
            return False, str(e)
        finally:
            sys.stdout = old_stdout

    loop = asyncio.get_running_loop()
    success, error_message = await loop.run_in_executor(executor, _run_trip_scraping)

    if success:
        scraped_cache = collected_data
        save_json_file(SCRAPED_FILE, scraped_cache)
        return JSONResponse({
            "status": "success",
            "count": len(scraped_cache),
            "cancelled": trip_cancelled,
            "items": scraped_cache
        })
    else:
        return JSONResponse({"status": "error", "message": error_message}, status_code=500)


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
        cursor.execute("UPDATE evaluated_items SET archived = 1 WHERE archived = 0")

    cursor.execute("UPDATE evaluated_items SET hidden = 0")
    
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
            cursor.execute("SELECT id, search_region, lat, lon, address FROM evaluated_items WHERE archived = 0 AND LOWER(TRIM(original_name)) = LOWER(?)", (orig_name,))
            candidates = cursor.fetchall()
        
        if not candidates and raw_name:
            cursor.execute("SELECT id, search_region, lat, lon, address FROM evaluated_items WHERE archived = 0 AND LOWER(TRIM(name)) = LOWER(?)", (raw_name,))
            candidates = cursor.fetchall()

        matched_existing = None
        for cand in candidates:
            c_lat = float(cand["lat"] or 0.0)
            c_lon = float(cand["lon"] or 0.0)

            if item_lat and item_lon and c_lat and c_lon:
                dist = haversine_distance(item_lat, item_lon, c_lat, c_lon)
                if dist <= 10.0:
                    matched_existing = cand
                    break
            else:
                matched_existing = cand
                break
        
        if not matched_existing:
            cursor.execute('''
                INSERT INTO evaluated_items (name, original_name, search_region, url, image, lat, lon, address, list_name, archived, hidden)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
            ''', (raw_name, orig_name, search_reg, item_url, item_img, item_lat, item_lon, item_addr, raw_list))
            added_count += 1
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
                    archived = 0,
                    hidden = 0
                WHERE id = ?
            ''', (item_url, item_img, orig_name, item_lat, item_lon, item_addr, merged_regions, matched_existing["id"]))
            duplicate_count += 1

    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM evaluated_items WHERE archived = 0")
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
               rating, personal_rating, global_rating, list_name, imported_list, archived, hidden, rating_comment 
        FROM evaluated_items WHERE archived = 0 ORDER BY id DESC
    """)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
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
    return JSONResponse(dict(row))


@app.post("/api/evaluated/{item_id}/update")
async def update_evaluated_item(item_id: int, request: Request):
    try:
        body = await request.json()
        list_name = body.get("list_name")
        rating_comment = body.get("rating_comment")
    except Exception:
        return JSONResponse({"status": "error", "message": "Некорректный JSON"}, status_code=400)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    updates = []
    params = []
    if list_name is not None:
        updates.append("list_name = ?")
        params.append(list_name.strip())
    if rating_comment is not None:
        updates.append("rating_comment = ?")
        params.append(rating_comment.strip())

    if updates:
        params.append(item_id)
        cursor.execute(f"UPDATE evaluated_items SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    conn.close()
    return JSONResponse({"status": "success", "id": item_id})


@app.post("/api/ai/rate_place")
async def rate_place_endpoint(request: Request):
    import asyncio
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
    # Передаем адрес в качестве основного локационного контекста вместо региона
    if full_desc:
        res = await loop.run_in_executor(executor, ai_service.rate_attraction, place_name, address, original_name, True, address)
    else:
        res = await loop.run_in_executor(executor, ai_service.quick_audit_attraction, place_name, address, original_name, address)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if item_id:
        cursor.execute('''
            UPDATE evaluated_items 
            SET rating = ?, personal_rating = ?, global_rating = ?, rating_comment = ?
            WHERE id = ?
        ''', (str(res.get("personal_rating", 0.0)), res.get("personal_rating", 0.0), res.get("global_rating", 0.0), res.get("comment", ""), item_id))
    else:
        cursor.execute('''
            UPDATE evaluated_items 
            SET rating = ?, personal_rating = ?, global_rating = ?, rating_comment = ?
            WHERE LOWER(name) = LOWER(?)
        ''', (str(res.get("personal_rating", 0.0)), res.get("personal_rating", 0.0), res.get("global_rating", 0.0), res.get("comment", ""), place_name))
    conn.commit()
    conn.close()

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
        cursor.execute(f"SELECT id, name, original_name, search_region, address FROM evaluated_items WHERE id IN ({placeholders}) AND (rating = '-' OR rating IS NULL) AND archived = 0", item_ids)
    else:
        cursor.execute("SELECT id, name, original_name, search_region, address FROM evaluated_items WHERE (rating = '-' OR rating IS NULL) AND archived = 0")
        
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
                    WHERE LOWER(name) = LOWER(?) AND archived = 0
                ''', (str(r_score), float(r_score), float(r_score), f"Блиц-оценка: {r_reason}", r_name))
                processed_count += 1
            conn.commit()
            conn.close()

    return JSONResponse({"status": "success", "processed_count": processed_count})


@app.delete("/api/evaluated/{item_id}")
def delete_evaluated_item(item_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE evaluated_items SET archived = 1 WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return JSONResponse({"status": "archived", "id": item_id})


@app.delete("/api/evaluated")
def clear_evaluated():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE evaluated_items SET archived = 1 WHERE imported_list != '' AND imported_list IS NOT NULL")
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
        
        cursor.execute("SELECT imported_list FROM evaluated_items WHERE id = ?", (item.get("id"),))
        row = cursor.fetchone()
        existing_lists = [l.strip() for l in (row[0] or "").split(",") if l.strip()] if row else []
        if raw_list and raw_list not in existing_lists:
            existing_lists.append(raw_list)
        merged_lists = ", ".join(existing_lists)

        cursor.execute("UPDATE evaluated_items SET imported_list = ? WHERE id = ?", (merged_lists, item.get("id")))

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
                "list_name": raw_list,
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

    success, message = automator.process_item(target, status_cb=update_step)

    if success:
        target["status"] = "Готово"
        target["error"] = ""
    else:
        if automator.is_cancelled or message == "Прервано пользователем":
            target["status"] = "Отменено"
            target["error"] = "Прервано пользователем"
        else:
            target["status"] = "Ошибка"
            target["error"] = message
    
    save_json_file(PLACES_FILE, places_cache)


@app.post("/api/process/{item_id}")
async def process_one(item_id: int):
    import asyncio
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(executor, _sync_process, item_id)

    target = next((x for x in places_cache if x["id"] == item_id), None)
    if not target:
        return JSONResponse({"status": "not_found", "id": item_id})
    return JSONResponse({"status": "success" if target.get("status") == "Готово" else "failed", "message": target.get("error", ""), "id": item_id})


@app.post("/api/stop")
async def stop_process():
    global places_cache
    automator.cancel()
    for item in places_cache:
        if item.get("status") not in ["В очереди", "Готово", "Ошибка", "Отменено"]:
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
                SET rating_comment = ? 
                WHERE id = ?
            """, (new_comment, item_id))
            conn.commit()
            
    conn.close()
    return JSONResponse({"status": "rejected", "id": item_id})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)