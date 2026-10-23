import json
import logging
import time
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GUIDE_PROMPT_FILE, RATING_PROMPT_FILE

logger = logging.getLogger("ai_service")
client = genai.Client(api_key=GEMINI_API_KEY)


def rate_attraction(place_name: str, region: str = "", original_name: str = "") -> dict:
    """
    Оценивает объект с умеренным thinking_budget=512 и автоповтором при 429/503.
    """
    if not RATING_PROMPT_FILE.exists():
        return {"personal_rating": 0.0, "global_rating": 0.0, "comment": f"Ошибка: файл {RATING_PROMPT_FILE.name} не найден"}

    with open(RATING_PROMPT_FILE, "r", encoding="utf-8") as f:
        rating_instruction = f.read().strip()

    orig_part = f' (оригинальное название / на местном языке: "{original_name}")' if original_name else ''
    user_query = f'Объект для оценки: "{place_name}"{orig_part} (локация / регион: "{region}").'

    config = types.GenerateContentConfig(
        system_instruction=rating_instruction,
        temperature=0.2,
        thinking_config=types.ThinkingConfig(thinking_budget=512),
        response_mime_type="application/json",
    )

    models_to_try = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
    last_err = ""

    for model_name in models_to_try:
        for attempt in range(1, 5):
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=user_query,
                    config=config,
                )
                data = json.loads(resp.text)
                return {
                    "personal_rating": float(data.get("personal_rating", 0.0)),
                    "global_rating": float(data.get("global_rating", 0.0)),
                    "comment": data.get("comment", "").strip()
                }
            except Exception as e:
                last_err = str(e)
                if "429" in last_err or "503" in last_err or "unavailable" in last_err.lower():
                    time.sleep(attempt * 2)
                    continue
                break  

    logger.error(f"Ошибка оценки объекта '{place_name}': {last_err}")
    return {
        "personal_rating": 0.0,
        "global_rating": 0.0,
        "comment": f"Ошибка AI: {last_err}"
    }

def verify_place_match(query: str, card_title: str, card_context: str, status_cb=None) -> tuple[bool, str]:
    def report(msg: str):
        if status_cb:
            status_cb(msg)
        logger.info(f"  [AI-Verify] {msg}")

    prompt = f"""
Ты — валидатор соответствия географических объектов для Google Maps.
Пользователь искал объект: "{query}".
На Google Maps открылась карточка:
- Основной заголовок: "{card_title}"
- Дополнительный контекст карточки (описание, категория, адрес): "{card_context}"

Задача: Определи, является ли открытая карточка именно тем объектом, который искал пользователь.
Учитывай:
1. Запрос может быть на любом языке (китайский/пиньинь и т.д.), а карточка на русском или английском.
2. Небольшие различия в категории допустимы ("мемориал" и "мавзолей", "монастырь" и "храм").
3. Если открылся совершенно другой объект (другой город, другое имя святыни), это не совпадение.

Ответь строго в формате JSON:
{{
  "is_match": true или false,
  "confidence": число от 0.0 до 1.0,
  "reason": "краткое объяснение на русском в 1 предложение"
}}
"""
    for attempt in range(1, 4):
        try:
            report(f"4/7: Валидация Gemini-Lite ({attempt}/3)...")
            resp = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            data = json.loads(resp.text)
            return data.get("is_match", False), data.get("reason", "")
        except Exception as e:
            if "503" in str(e) or "429" in str(e) and attempt < 3:
                report("4/7: Модель перегружена (429/503). Пауза 2 сек...")
                time.sleep(2)
                continue
            return False, f"Ошибка валидации API: {e}"
    return False, "Превышен лимит попыток валидации"


def generate_guide(place_name: str, card_title: str, card_context: str, status_cb=None, check_cancelled=None) -> tuple[str, str]:
    def report(msg: str):
        if status_cb:
            status_cb(msg)
        logger.info(f"  [AI-Guide] {msg}")

    if not GUIDE_PROMPT_FILE.exists():
        return "", "Файл travel_guide_prompt.md не найден"

    if check_cancelled and check_cancelled():
        return "", "Прервано пользователем"

    with open(GUIDE_PROMPT_FILE, "r", encoding="utf-8") as f:
        skill_instruction = f.read().strip()

    user_query = f"{place_name} ({card_title}). Контекст карточки: {card_context}"

    text = ""
    last_err = ""
    max_attempts = 3

    config_guide = types.GenerateContentConfig(
        system_instruction=skill_instruction,
        temperature=0.4,
        thinking_config=types.ThinkingConfig(thinking_budget=1024),
    )

    for attempt in range(1, max_attempts + 1):
        if check_cancelled and check_cancelled():
            return "", "Прервано пользователем"
            
        try:
            report(f"7/7: Генерация gemini-3.5-flash ({attempt}/{max_attempts})...")
            start_t = time.time()
            resp = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=user_query,
                config=config_guide,
            )
            text = resp.text.strip()
            elapsed = round(time.time() - start_t, 1)
            report(f"7/7: Текст сгенерирован за {elapsed} сек. ({len(text)} зн.).")
            break
        except Exception as e:
            last_err = str(e)
            if ("503" in last_err or "429" in last_err or "unavailable" in last_err.lower()) and attempt < max_attempts:
                time.sleep(attempt * 2)
                continue
            break

    if not text:
        return "", f"Ошибка генерации gemini-3.5-flash: {last_err}"

    if len(text) > 3600:
        if check_cancelled and check_cancelled():
            return text, ""
            
        report(f"7/7: Сжатие текста ({len(text)} > 3600 зн.) под лимит Google Maps...")
        compress_instruction = "Ты — технический редактор. Сжми текст до 3200-3400 символов, сохранив все секции и факты."
        try:
            comp_resp = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=f"Сократи следующий текст строго до 3200-3400 символов:\n\n{text}",
                config=types.GenerateContentConfig(
                    system_instruction=compress_instruction,
                    temperature=0.2,
                    thinking_config=types.ThinkingConfig(thinking_budget=512),
                ),
            )
            if comp_resp.text.strip():
                text = comp_resp.text.strip()
        except Exception:
            pass

    return text, ""