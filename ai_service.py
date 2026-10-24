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

    models_to_try = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
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


def rate_attractions_batch(places: list[dict]) -> list[dict]:
    """
    Пакетная блиц-оценка списка объектов в ультракомпактном формате (массив массивов) для экономии токенов.
    """
    if not places:
        return []
    
    items_input = []
    for p in places:
        items_input.append({
            "name": p.get("name"),
            "original_name": p.get("original_name", ""),
            "region": p.get("search_region", "")
        })

    prompt = f"""
Ты — жесткий туристический аудитор автопутешествий. Оцени объекты Китая от 1.0 до 10.0 (10 — мировой феномен, 7.0-8.9 — ключевые природные/исторические доминанты, 6.0-6.9 — стоящее наполнение дня, 4.0-5.9 — слабый проходняк, <4.0 — шлак/ловушка).

Критерии:
+ В плюс: горы/скалы/обрывы, подлинная древность 600+ лет, крепости/стены, масштабные каньоны/пустыни, крупные топ-музеи.
- В минус: коммерция, агропарки, покатушки, мелкие храмы, городские парки/площади, части других комплексов.
! Жесткий отсев (<5.0): кемпинги/глэмпинги, отели, турбазы отдыха (Resort/Camp), питомники/зооуголки, плоские поля/степи без рельефа.

Формат: строго JSON-массив массивов без разметки markdown и пояснений:
[["Имя из списка", 6.5, "краткая суть до 8 слов"]]

Список объектов для оценки:
{json.dumps(items_input, ensure_ascii=False)}
"""

    config = types.GenerateContentConfig(
        temperature=0.1,
        response_mime_type="application/json"
    )

    try:
        resp = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=config
        )
        
        raw_text = resp.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        
        raw_data = json.loads(raw_text.strip())
        
        structured_results = []
        for row in raw_data:
            if isinstance(row, list) and len(row) >= 3:
                structured_results.append({
                    "name": row[0],
                    "rating": row[1],
                    "reason": row[2]
                })
        return structured_results

    except Exception as e:
        logger.error(f"Ошибка пакетной блиц-оценки: {e}")
        return []