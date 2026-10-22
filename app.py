import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from config import PLACES_FILE
from maps_automator import MapsAutomator

automator = MapsAutomator()
executor = ThreadPoolExecutor(max_workers=1)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    automator.stop()


app = FastAPI(lifespan=lifespan)


def load_places():
    if not PLACES_FILE.exists():
        return []
    with open(PLACES_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            for idx, item in enumerate(data):
                if "id" not in item:
                    item["id"] = idx + 1
                if "status" not in item:
                    item["status"] = "В очереди"
                if "error" not in item:
                    item["error"] = ""
            return data
        except Exception:
            return []


def save_places(data):
    clean_data = [
        {
            "name": x.get("name") or x.get("query"),
            "list_name": x.get("list_name"),
            "status": x.get("status", "В очереди"),
            "error": x.get("error", "")
        }
        for x in data
    ]
    with open(PLACES_FILE, "w", encoding="utf-8") as f:
        json.dump(clean_data, f, ensure_ascii=False, indent=2)


places_cache = load_places()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/places")
def get_places(reload: bool = False):
    global places_cache
    if reload:
        fresh_places = load_places()
        existing_status = {
            (x.get("name") or x.get("query")): (x.get("status"), x.get("error"))
            for x in places_cache
        }
        for item in fresh_places:
            key = item.get("name") or item.get("query")
            if key in existing_status:
                item["status"], item["error"] = existing_status[key]
                
        places_cache = fresh_places

    return JSONResponse(places_cache)


def _sync_process(item_id: int):
    global places_cache
    target = next((x for x in places_cache if x["id"] == item_id), None)
    if not target:
        return

    name = target.get("name") or target.get("query")
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
        # Если прервано пользователем, ставим статус "Отменено"
        if automator.is_cancelled or message == "Прервано пользователем":
            target["status"] = "Отменено"
            target["error"] = "Прервано пользователем"
        else:
            target["status"] = "Ошибка"
            target["error"] = message
    
    save_places(places_cache)


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
    save_places(places_cache)
    return JSONResponse({"status": "stopped"})


@app.delete("/api/places/{item_id}")
def delete_place(item_id: int):
    global places_cache
    places_cache = [x for x in places_cache if x["id"] != item_id]
    save_places(places_cache)
    return JSONResponse({"status": "deleted", "id": item_id})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)