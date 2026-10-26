import json
import logging
import re
import time
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GUIDE_PROMPT_FILE, AUDIT_PROMPT_FILE

logger = logging.getLogger("ai_service")
client = genai.Client(api_key=GEMINI_API_KEY)


def quick_audit_attraction(place_name: str, region: str = "", original_name: str = "", address: str = "") -> dict:
    """
    Экспресс-аудит объекта:
    - Требует наличия файла AUDIT_PROMPT_FILE. Если файла нет — возвращает ошибку.
    - Учитывает адрес вместо региона для оценки ценности самого места.
    """
    if not AUDIT_PROMPT_FILE.exists():
        err_msg = f"Ошибка: файл промпта аудита {AUDIT_PROMPT_FILE.name} не найден"
        logger.error(err_msg)
        return {"personal_rating": 0.0, "global_rating": 0.0, "comment": err_msg}

    with open(AUDIT_PROMPT_FILE, "r", encoding="utf-8") as f:
        audit_instruction = f.read().strip()

    orig_part = f' (оригинальное название: "{original_name}")' if original_name else ''
    addr_part = f', точный адрес: "{address}"' if address else f', регион: "{region}"'
    user_query = f'Объект: "{place_name}"{orig_part}{addr_part}. Оценивай ценность самого объекта, не отсеивай за удаленность. Дай экспресс-аудит.'

    config = types.GenerateContentConfig(
        system_instruction=audit_instruction,
        temperature=0.0,
        max_output_tokens=180,
    )

    try:
        resp = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=user_query,
            config=config,
        )
        text_resp = resp.text.strip()
    except Exception as e:
        logger.error(f"Ошибка экспресс-аудита для '{place_name}': {e}")
        return {"personal_rating": 0.0, "global_rating": 0.0, "comment": f"Ошибка AI: {e}"}

    personal_rating = 0.0
    global_rating = 0.0

    m_pr = re.search(r'Личный рейтинг:\s*(\d+\.?\d*)', text_resp)
    if m_pr:
        try:
            personal_rating = float(m_pr.group(1))
        except:
            pass

    m_gr = re.search(r'Глобальный рейтинг:\s*(\d+\.?\d*)', text_resp)
    if m_gr:
        try:
            global_rating = float(m_gr.group(1))
        except:
            pass

    return {
        "personal_rating": personal_rating,
        "global_rating": global_rating,
        "comment": text_resp
    }


def rate_attraction(place_name: str, region: str = "", original_name: str = "", full_description: bool = False, address: str = "", status_cb=None) -> dict:
    """
    Генерирует подробное описание по вашему скиллу. 
    Включает двухпроходную логику: если текст превышает 4000 символов, запускается второй проход для сжатия.
    """
    def report(msg):
        if status_cb:
            status_cb(msg)
        logger.info(f"  [AI-Guide] {msg}")

    if not GUIDE_PROMPT_FILE.exists():
        err_msg = f"Ошибка: файл гайда {GUIDE_PROMPT_FILE.name} не найден"
        logger.error(err_msg)
        return {"personal_rating": 0.0, "global_rating": 0.0, "comment": err_msg}

    with open(GUIDE_PROMPT_FILE, "r", encoding="utf-8") as f:
        skill_instruction = f.read().strip()

    orig_part = f' (оригинальное название: "{original_name}")' if original_name else ''
    addr_part = f', локация/адрес: "{address}"' if address else f', регион: "{region}"'
    mode_str = "Режим: Полное подробное описание (шаблон строго до 4000 символов)" if full_description else "Режим: Оценка и первичное описание по шкале интереса"
    user_query = f'Объект: "{place_name}"{orig_part}{addr_part}. Не занижай оценку из-за удаленности или логистики. {mode_str}'

    config = types.GenerateContentConfig(
        system_instruction=skill_instruction,
        temperature=0.3,
        thinking_config=types.ThinkingConfig(thinking_budget=1024),
        max_output_tokens=2800,
    )

    models_to_try = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    last_err = ""
    text_resp = ""

    report("Генерация подробного описания...")
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

    # Проверка длины текста и второй проход (сокращение при превышении 4000 символов)
    if full_description and len(text_resp) > 4000:
        report(f"Текст превысил лимит ({len(text_resp)} зн.). Сокращаем текст (второй проход)...")
        compress_prompt = f"""
Отредактируй и сократи следующий текст об объекте «{place_name}», чтобы его общая длина строго не превышала 4000 символов, но не была меньше 3500 символов. 
Сохрани всю ключевую историческую информацию, структуру шаблона, рейтинги и полезные советы, но убери «воды» и избыточные описания.
ВАЖНО: Выдавай ТОЛЬКО итоговый текст гайда в формате Markdown. Запрещено писать любые вступительные фразы, комментарии вроде 'Вот отредактированный текст...', метаданные или рассуждения. Начало ответа сразу с названия объекта.

Исходный текст для сокращения:
{text_resp}
"""
        for attempt in range(1, 3):
            try:
                comp_resp = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=compress_prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=1500,
                    )
                )
                comp_text = comp_resp.text.strip()
                if comp_text:
                    text_resp = comp_text
                    report(f"Текст успешно сокращен до {len(text_resp)} символов.")
                    break
            except Exception as e:
                logger.error(f"Ошибка при сжатии текста: {e}")
                if attempt == 2:
                    report("Не удалось выполнить сжатие, используется оригинальный текст.")

    personal_rating = 0.0
    global_rating = 0.0
    
    m_pr = re.search(r'(?:личн\w*|personal)[^\d\n]*?(\d+\.\d+|\d+)', text_resp, re.IGNORECASE)
    if m_pr:
        try:
            personal_rating = float(m_pr.group(1))
        except:
            pass

    m_gr = re.search(r'(?:глобал\w*|global)[^\d\n]*?(\d+\.\d+|\d+)', text_resp, re.IGNORECASE)
    if m_gr:
        try:
            global_rating = float(m_gr.group(1))
        except:
            pass

    if personal_rating == 0.0 or global_rating == 0.0:
        rating_section_match = re.search(r'\*\*Рейтинг:\*\*(.*?)(?=\*\*Полезная|\Z)', text_resp, re.DOTALL | re.IGNORECASE)
        if rating_section_match:
            sec_text = rating_section_match.group(1)
            found_nums = re.findall(r'(\d+\.\d+|\d+)', sec_text)
            if found_nums:
                if personal_rating == 0.0 and len(found_nums) > 0:
                    personal_rating = float(found_nums[0])
                if global_rating == 0.0 and len(found_nums) > 1:
                    global_rating = float(found_nums[1])

    return {
        "personal_rating": personal_rating,
        "global_rating": global_rating,
        "comment": text_resp
    }


def verify_place_match(place_name: str, original_name: str, geo_context: str, card_title: str) -> tuple[bool, str]:
    names_str = f'"{place_name}"'
    if original_name and original_name != place_name:
        names_str += f' (ориг. "{original_name}")'

    prompt = f"""
Гео-валидатор карт. 
Искомый объект: {names_str}. 
Ожидаемый региональный контекст: "{geo_context or 'не указан'}".
Найденная карточка на картах: "{card_title}".

Правила проверки:
1. Кросс-языковое соответствие: название на карте может быть на любом языке или письменности (иероглифы, пиньинь и т.д.) и являться переводом.
2. Игнорируй разницу в суффиксах и типах (парк, музей, temple и т.п.).
3. РЕГИОНЫ И ГОРОДА: В туристических базах объекты могут числиться за общим регионом, автономным районом или соседним крупным городом, но физически находиться в другом городе или уезде этой же провинции/области. Если название и суть объекта совпадают, разница в городах внутри одного региона/провинции НЕ является ошибкой — это тот самый объект.
4. Отвечай true, если это один объект. False — только если это принципиально другое место в другой части страны или мира.

Ответь строго в JSON:
{{
  "is_match": true или false,
  "reason": "краткая причина решения в 1 предложение на русском"
}}
"""
    for attempt in range(1, 3):
        try:
            resp = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                    max_output_tokens=150,
                ),
            )
            data = json.loads(resp.text)
            is_matched = data.get("is_match", False)
            reason = data.get("reason", "Причина не указана")
            return is_matched, reason
        except Exception as e:
            if attempt == 1:
                time.sleep(1)
                continue
            return False, f"Ошибка валидации: {e}"
    return False, "Превышен лимит попыток"

def rate_attractions_batch(places: list[dict]) -> list[dict]:
    """
    Пакетная блиц-оценка списка объектов.
    """
    if not places:
        return []
    
    items_input = []
    for p in places:
        item_entry = {
            "name": p.get("name"),
            "original_name": p.get("original_name", ""),
            "address": p.get("address", "") or p.get("search_region", "")
        }
        items_input.append(item_entry)

    prompt = f"""
Ты — жесткий туристический аудитор автопутешествий. Оцени объекты от 1.0 до 10.0 (10 — мировой феномен, 7.0-8.9 — ключевые доминанты, 6.0-6.9 — стоящее наполнение дня, 4.0-5.9 — проходняк, <4.0 — ловушка).
ВАЖНО: Оценивай объективную историческую/туристическую ценность самого места. Не занижай оценку из-за удаленности или сложной логистики (пользователь сам решает, ехать ли туда).

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