import json
import logging
import re
import time
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GUIDE_PROMPT_FILE, AUDIT_PROMPT_FILE

logger = logging.getLogger("ai_service")
client = genai.Client(api_key=GEMINI_API_KEY)


TOTAL_SEARCH_QUERIES = 0

def _log_usage(operation_name: str, resp, elapsed: float):
    global TOTAL_SEARCH_QUERIES

    usage = getattr(resp, "usage_metadata", None)
    in_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
    out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
    total_tok = getattr(usage, "total_token_count", 0) if usage else 0

    # Проверяем оба возможных места размещения grounding_metadata
    g_meta = getattr(resp, "grounding_metadata", None)
    if not g_meta:
        candidates = getattr(resp, "candidates", [])
        if candidates:
            g_meta = getattr(candidates[0], "grounding_metadata", None)

    search_queries = []
    if g_meta:
        # Пробуем как атрибут объекта или как ключ словаря
        if hasattr(g_meta, "web_search_queries"):
            search_queries = g_meta.web_search_queries or []
        elif isinstance(g_meta, dict):
            search_queries = g_meta.get("web_search_queries", [])

    q_count = len(search_queries)
    TOTAL_SEARCH_QUERIES += q_count

    # Всегда выводим Search queries, даже если их 0, чтобы видеть работу трекера
    logger.info(
        f"⚡ [{operation_name}] {elapsed:.2f}s | "
        f"Токены: вход={in_tok}, выход={out_tok}, всего={total_tok} | "
        f"Search: {q_count} (сессия: {TOTAL_SEARCH_QUERIES})"
    )


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
    orig_part = f' (original name: "{original_name}")' if original_name else ''
    addr_part = f', address: "{address}"' if address else f', region: "{region}"'
    user_query = f'Target place: "{place_name}"{orig_part}{addr_part}. Provide quick audit.'

    config = types.GenerateContentConfig(
        system_instruction=audit_instruction,
        temperature=0.0,
        max_output_tokens=180,
    )

    t0 = time.perf_counter()
    try:
        resp = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=user_query,
            config=config,
        )
        elapsed = time.perf_counter() - t0
        _log_usage(f"Экспресс-аудит: {place_name}", resp, elapsed)
        text_resp = resp.text.strip()
    except Exception as e:
        elapsed = time.perf_counter() - t0
        logger.error(f"Ошибка экспресс-аудита для '{place_name}' ({elapsed:.2f}s): {e}")
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

    orig_part = f' (original name: "{original_name}")' if original_name else ''
    addr_part = f', location/address: "{address}"' if address else f', region: "{region}"'
    mode_str = "Mode: Full in-depth guide (strictly up to 3800 characters)" if full_description else "Mode: Score and primary overview by rating scale"
    user_query = f'Target place: "{place_name}"{orig_part}{addr_part}. Do not penalize score for distance or logistics. {mode_str}'

    config = types.GenerateContentConfig(
        system_instruction=skill_instruction,
        temperature=0.3,
        thinking_config=types.ThinkingConfig(thinking_budget=128),
        max_output_tokens=2500,
    )

    models_to_try = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    last_err = ""
    text_resp = ""

    report("Генерация подробного описания...")
    for model_name in models_to_try:
        for attempt in range(1, 4):
            t0 = time.perf_counter()
            try:
                resp = client.models.generate_content(
                    model=model_name,
                    contents=user_query,
                    config=config,
                )
                elapsed = time.perf_counter() - t0
                _log_usage(f"Гайд ({model_name}): {place_name}", resp, elapsed)
                text_resp = resp.text.strip()
                if text_resp:
                    break
            except Exception as e:
                elapsed = time.perf_counter() - t0
                last_err = str(e)
                logger.warning(f"Попытка {attempt} модели {model_name} завершилась ошибкой ({elapsed:.2f}s): {e}")
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
    if full_description and len(text_resp) > 4050:
        report(f"Текст превысил лимит ({len(text_resp)} зн.). Сокращаем текст (второй проход)...")
        compress_prompt = f"""
Отредактируй и сократи следующий текст об объекте «{place_name}», чтобы его общая длина строго не превышала 4000 символов. 
Сохрани всю ключевую историческую информацию, структуру шаблона, рейтинги и полезные советы, но убери «воду» и избыточные описания. Сократи текущий текст примерно на 15–20%.
ВАЖНО: Выдавай ТОЛЬКО итоговый текст гайда в формате Markdown. Запрещено писать любые вступительные фразы, комментарии вроде 'Вот отредактированный текст...', метаданные или рассуждения. Начало ответа сразу с названия объекта.

Исходный текст для сокращения:
{text_resp}
"""
        for attempt in range(1, 3):
            t0 = time.perf_counter()
            try:
                comp_resp = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=compress_prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=1500,
                    )
                )
                elapsed = time.perf_counter() - t0
                _log_usage(f"Сжатие текста: {place_name}", comp_resp, elapsed)
                comp_text = comp_resp.text.strip()
                if comp_text:
                    text_resp = comp_text
                    report(f"Текст успешно сокращен до {len(text_resp)} символов.")
                    break
            except Exception as e:
                elapsed = time.perf_counter() - t0
                logger.error(f"Ошибка при сжатии текста ({elapsed:.2f}s): {e}")
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
        names_str += f' (orig. "{original_name}")'

    prompt = f"""
Maps geospatial validator.
Target place: {names_str}.
Expected regional context: "{geo_context or 'not specified'}".
Found Maps card title: "{card_title}".

Validation rules:
1. Cross-language matching: the name on the card may be in any language or transliterated/translated into the local language.
2. ENTRANCES & INFRASTRUCTURE: Official gates, ticket offices, parking lots, or visitor information centers belonging to the target attraction MUST be considered a valid match (true).
3. SCALE & CONTEXT: If the target attraction is a major nature park, reserve, or scenic area, the card for the park/reserve itself MUST be considered a valid match (true), even if the query specifies a particular street or village (as they are inside or right at the entrance).
4. STOP FACTORS: Reject (false) unrelated commercial or recreational facilities (hotels, restaurants, sports fields) unless they are directly part of the attraction itself.
5. Return true if this is the attraction itself, its official entrance, or its representative scenic area/complex.

Respond strictly in JSON:
{{
  "is_match": true or false,
  "reason": "brief reason for the decision in 1 sentence in Russian"
}}
"""
    for attempt in range(1, 3):
        t0 = time.perf_counter()
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
            elapsed = time.perf_counter() - t0
            _log_usage(f"Валидация карточки: {place_name}", resp, elapsed)
            data = json.loads(resp.text)
            is_matched = data.get("is_match", False)
            reason = data.get("reason", "Причина не указана")
            return is_matched, reason
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.warning(f"Ошибка попытки {attempt} валидации ({elapsed:.2f}s): {e}")
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

    t0 = time.perf_counter()
    try:
        resp = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=config
        )
        elapsed = time.perf_counter() - t0
        _log_usage("Блиц-оценка пачки", resp, elapsed)
        
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
        elapsed = time.perf_counter() - t0
        logger.error(f"Ошибка пакетной блиц-оценки ({elapsed:.2f}s): {e}")
        return []