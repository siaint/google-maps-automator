import json
import logging
import time
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GUIDE_PROMPT_FILE, RATING_PROMPT_FILE

logger = logging.getLogger("ai_service")
client = genai.Client(api_key=GEMINI_API_KEY)


def rate_attraction(place_name: str, region: str = "", original_name: str = "", full_description: bool = False) -> dict:
    """
    Оценивает объект и генерирует описание по вашему скиллу (включая жесткий отсев <4.0 и полный шаблон до 3800 символов).
    """
    if not GUIDE_PROMPT_FILE.exists():
        return {"personal_rating": 0.0, "global_rating": 0.0, "comment": f"Ошибка: файл {GUIDE_PROMPT_FILE.name} не найден"}

    with open(GUIDE_PROMPT_FILE, "r", encoding="utf-8") as f:
        skill_instruction = f.read().strip()

    orig_part = f' (оригинальное название: "{original_name}")' if original_name else ''
    mode_str = "Режим: Полное подробное описание (шаблон до 3800 символов)" if full_description else "Режим: Оценка и первичное описание по шкале интереса"
    user_query = f'Объект: "{place_name}"{orig_part} (регион: "{region}"). {mode_str}'

    config = types.GenerateContentConfig(
        system_instruction=skill_instruction,
        temperature=0.3,
        thinking_config=types.ThinkingConfig(thinking_budget=1024),
    )

    models_to_try = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
    last_err = ""
    text_resp = ""

    for model_name in models_to_try:
        for attempt in range(1, 4):
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=user_query,
                    config=config,
                )
                text_resp = resp.text.strip()
                if text_resp:
                    break
            except Exception as e:
                last_err = str(e)
                if "429" in last_err or "503" in last_err or "unavailable" in last_err.lower():
                    time.sleep(attempt * 2)
                    continue
                break
        if text_resp:
            break

    if not text_resp:
        logger.error(f"Ошибка генерации для '{place_name}': {last_err}")
        return {
            "personal_rating": 0.0,
            "global_rating": 0.0,
            "comment": f"Ошибка AI: {last_err}"
        }

    # Извлекаем личный рейтинг из текста для бейджа в таблице (ищем числа в формате X.X или Личный рейтинг)
    personal_rating = 5.0
    global_rating = 5.0
    
    for line in text_resp.split('\n'):
        if "Личный рейтинг" in line or "PERSONAL_RATING" in line:
            import re
            nums = re.findall(r'\d+\.\d+|\d+', line)
            if nums:
                try:
                    personal_rating = float(nums[0])
                except:
                    pass
        elif "Глобальный рейтинг" in line or "GLOBAL_RATING" in line:
            import re
            nums = re.findall(r'\d+\.\d+|\d+', line)
            if nums:
                try:
                    global_rating = float(nums[0])
                except:
                    pass

    return {
        "personal_rating": personal_rating,
        "global_rating": global_rating,
        "comment": text_resp
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