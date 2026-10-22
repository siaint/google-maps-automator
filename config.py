import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "google_maps_profile"
PLACES_FILE = BASE_DIR / "places.json"
GUIDE_PROMPT_FILE = BASE_DIR / "travel_guide_prompt.md"
SCREENSHOTS_DIR = BASE_DIR / "debug_screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")