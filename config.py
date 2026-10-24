import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "google_maps_profile"

# Файлы данных
PLACES_FILE = BASE_DIR / "places.json"                  # Вкладка 3: Импорт в карты
SCRAPED_FILE = BASE_DIR / "scraped_data.json"          # Вкладка 1: Скрейпер (временный буфер)
DB_FILE = BASE_DIR / "travel_master.db"                # База данных SQLite для оценки и архива

GUIDE_PROMPT_FILE = BASE_DIR / "travel_guide_prompt.md"
AUDIT_PROMPT_FILE = BASE_DIR / "audit_prompt.md"
SCREENSHOTS_DIR = BASE_DIR / "debug_screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
