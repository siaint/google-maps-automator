import json
import sys
import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from config import PLACES_FILE, SCRAPED_FILE, EVALUATED_FILE
from maps_automator import MapsAutomator
from scrapers.trip_scraper import scrape_trip_attractions
import ai_service

automator = MapsAutomator()
executor = ThreadPoolExecutor(max_workers=3)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

trip_cancelled = False

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


# Универсальные функции загрузки/сохранения
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


# Кэши для трех вкладок
scraped_cache = load_json_file(SCRAPED_FILE)
evaluated_cache = load_json_file(EVALUATED_FILE)
places_cache = load_json_file(PLACES_FILE)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# --- Эндпоинты Вкладки 1: Скрейпер ---
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
    print("\n[!] Сигнал отмены отправлен в скрейпер...")
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


# Перенос из Скрейпера (Вкладка 1) в Оценку (Вкладка 2)
@app.post("/api/scraped/transfer_to_evaluated")
async def transfer_to_evaluated(request: Request):
    global evaluated_cache
    try:
        items = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Некорректный JSON"}, status_code=400)

    existing_pairs = {
        (
            (x.get("name") or "").strip().lower(),
            (x.get("list_name") or "Хочу посетить").strip().lower()
        )
        for x in evaluated_cache
    }
    
    added_count = 0
    for item in items:
        raw_name = (item.get("name") or "").strip()
        raw_list = (item.get("list_name") or "Хочу посетить").strip()
        if not raw_name:
            continue
        
        pair_key = (raw_name.lower(), raw_list.lower())
        if pair_key not in existing_pairs:
            evaluated_cache.append({
                "id": len(evaluated_cache) + 1,
                "name": raw_name,
                "rating": item.get("rating", "-"),
                "rating_comment": item.get("rating_comment", ""),
                "list_name": raw_list
            })
            existing_pairs.add(pair_key)
            added_count += 1

    save_json_file(EVALUATED_FILE, evaluated_cache)
    return JSONResponse({"status": "success", "added_count": added_count, "total_count": len(evaluated_cache)})


# --- Эндпоинты Вкладки 2: Оценка ИИ ---
@app.get("/api/evaluated")
def get_evaluated():
    global evaluated_cache
    return JSONResponse(evaluated_cache)


@app.post("/api/ai/rate_place")
async def rate_place_endpoint(request: Request):
    import asyncio
    global evaluated_cache
    try:
        body = await request.json()
        place_name = body.get("name", "").strip()
        region = body.get("region", "").strip()
    except Exception:
        return JSONResponse({"status": "error", "message": "Некорректный JSON"}, status_code=400)

    if not place_name:
        return JSONResponse({"status": "error", "message": "Не указано имя объекта"}, status_code=400)

    # Запускаем оценку через ИИ
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(executor, ai_service.rate_attraction, place_name, region)

    # Находим объект в evaluated_cache и обновляем его данные
    updated = False
    for item in evaluated_cache:
        if item.get("name", "").strip().lower() == place_name.lower():
            item["personal_rating"] = res.get("personal_rating", 0.0)
            item["global_rating"] = res.get("global_rating", 0.0)
            item["rating_comment"] = res.get("comment", "")
            updated = True
            break

    # Если вдруг объекта не было в кэше, добавляем его
    if not updated:
        evaluated_cache.append({
            "id": len(evaluated_cache) + 1,
            "name": place_name,
            "personal_rating": res.get("personal_rating", 0.0),
            "global_rating": res.get("global_rating", 0.0),
            "rating_comment": res.get("comment", ""),
            "list_name": region or "Хочу посетить"
        })

    # Обязательно сохраняем на диск в evaluated_data.json
    save_json_file(EVALUATED_FILE, evaluated_cache)

    return JSONResponse({"status": "success", "result": res})


@app.delete("/api/evaluated/{item_id}")
def delete_evaluated_item(item_id: int):
    global evaluated_cache
    evaluated_cache = [x for x in evaluated_cache if x["id"] != item_id]
    save_json_file(EVALUATED_FILE, evaluated_cache)
    return JSONResponse({"status": "deleted", "id": item_id})


@app.delete("/api/evaluated")
def clear_evaluated():
    global evaluated_cache
    evaluated_cache = []
    save_json_file(EVALUATED_FILE, evaluated_cache)
    return JSONResponse({"status": "cleared"})


# Перенос из Оценки (Вкладка 2) в Импорт карт (Вкладка 3)
@app.post("/api/evaluated/transfer_to_places")
async def transfer_to_places(request: Request):
    global places_cache
    try:
        items = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Некорректный JSON"}, status_code=400)

    existing_pairs = {
        (
            (x.get("name") or "").strip().lower(),
            (x.get("list_name") or "Хочу посетить").strip().lower()
        )
        for x in places_cache
    }
    
    added_count = 0
    for item in items:
        raw_name = (item.get("name") or "").strip()
        raw_list = (item.get("list_name") or "Хочу посетить").strip()
        if not raw_name:
            continue
        
        pair_key = (raw_name.lower(), raw_list.lower())
        if pair_key not in existing_pairs:
            places_cache.append({
                "id": len(places_cache) + 1,
                "name": raw_name,
                "rating": item.get("rating", "-"),
                "rating_comment": item.get("rating_comment", ""),
                "list_name": raw_list,
                "status": "В очереди",
                "error": ""
            })
            existing_pairs.add(pair_key)
            added_count += 1

    save_json_file(PLACES_FILE, places_cache)
    return JSONResponse({"status": "success", "added_count": added_count, "total_count": len(places_cache)})


# --- Эндпоинты Вкладки 3: Импорт в карты ---
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

    name = target.get("name")
    list_name = target.get("list_name", "Хочу посетить")

    def update_step(step_name: str):
        if automator.is_cancelled:
            return
        target["status"] = step_name
        target["error"] = ""

    success, message = automator.process_item(name, list_name, status_cb=update_step)

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
    places_cache = []
    save_json_file(PLACES_FILE, places_cache)
    return JSONResponse({"status": "cleared"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)