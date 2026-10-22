import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from google import genai
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path("./google_maps_profile")
LOG_FILE = Path("maps_processing.log")
REPORT_FILE = Path("processing_report.json")
GUIDE_PROMPT_FILE = Path("travel_guide_prompt.md")
SCREENSHOTS_DIR = Path("./debug_screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

client = genai.Client(api_key="AQ.Ab8RN6KiM_pIdCH641qCVSgfxsNAY3DT0PdW-0LZ99OPZyFj9A")


def save_debug_screenshot(page, prefix: str) -> str:
    timestamp = datetime.now().strftime("%H%M%S")
    clean_prefix = re.sub(r"[^\w\-]", "_", prefix)[:30]
    filename = SCREENSHOTS_DIR / f"{clean_prefix}_{timestamp}.png"
    try:
        page.screenshot(path=str(filename), full_page=False)
        return str(filename.resolve())
    except Exception as e:
        return f"Не удалось сделать скриншот: {e}"


import time

def verify_place_match_with_ai(query: str, card_title: str, card_context: str) -> tuple[bool, str]:
    prompt = f"""
Ты — валидатор соответствия географических объектов для Google Maps.
Пользователь искал объект: "{query}".
На Google Maps открылась карточка:
- Основной заголовок: "{card_title}"
- Дополнительный контекст карточки (описание, категория, адрес): "{card_context}"

Задача: Определи, является ли открытая карточка именно тем объектом, который искал пользователь.
Учитывай:
1. Запрос может быть на любом языке, например, китайском (иероглифы/пиньинь), а карточка на русском или английском.
2. Небольшие различия в категории допустимы (например, "мемориал" и "мавзолей", "монастырь" и "храм").
3. Если открылся совершенно другой объект (другой город, другое имя святыни), это не совпадение.

Ответь строго в формате JSON:
{{
  "is_match": true или false,
  "confidence": число от 0.0 до 1.0,
  "reason": "краткое объяснение на русском в 1 предложение"
}}
"""
    # Добавляем 3 попытки на случай временного 503 Unavailable
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            data = json.loads(response.text)
            return data.get("is_match", False), data.get("reason", "")
        except Exception as e:
            if "503" in str(e) and attempt < 3:
                logging.warning(f"  [AI-Lite] Сервер временно перегружен (503). Повтор через 2 сек (попытка {attempt}/3)...")
                time.sleep(2)
                continue
            logging.warning(f"  [AI-Lite] Сбой API ИИ: {e}. Используем fallback.")
            return False, f"Ошибка API ИИ: {e}"
            
    return False, "Превышено число попыток обращения к ИИ"


def generate_travel_guide_note(place_name: str, card_title: str, card_context: str) -> str:
    """Генерирует описание строго по скиллу с максимизацией объема под полные 4000 знаков."""
    if not GUIDE_PROMPT_FILE.exists():
        logging.error(f"  [AI-Guide] Файл инструкций {GUIDE_PROMPT_FILE} не найден на диске!")
        return ""

    with open(GUIDE_PROMPT_FILE, "r", encoding="utf-8") as pf:
        skill_instruction = pf.read().strip()

    user_query = f"{place_name} ({card_title}). Контекст карточки: {card_context}"

    text = ""
    # 1. Чистая генерация по скиллу с защитой от 503
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=user_query,
                config={
                    "system_instruction": skill_instruction,
                },
            )
            text = response.text.strip()
            logging.info(f"      [AI-Guide] Гайд сформирован по скиллу: {len(text)} знаков.")
            break
        except Exception as e:
            if "503" in str(e) and attempt < 3:
                wait_time = attempt * 2.5
                logging.warning(f"  [AI-Guide] Модель перегружена (503). Повтор через {wait_time} сек (попытка {attempt}/3)...")
                time.sleep(wait_time)
                continue
            logging.warning(f"  [AI-Guide] Ошибка при вызове gemini-3.8-flash: {e}")
            break

    if not text:
        return ""

    # 2. Сжатие включается только при превышении абсолютного лимита Google Maps (4000 знаков)
    if len(text) > 4000:
        logging.info(f"      [AI-Guide] Текст ({len(text)} зн.) превышает 4000 знаков. Подгоняем под 3950-4000...")
        compress_instruction = """
Ты — технический редактор. Твоя единственная задача — аккуратно подогнать переданный готовый текст под диапазон 3950-4000 символов для карточки Google Maps.

ПРАВИЛА:
1. Сохрани абсолютно все секции и оригинальную структуру гайда.
2. Оставь как можно более полными вводный абзац, историческую справку, архитектурные особенности и аргументацию рейтингов. Сократи остальные разделы.
3. Сократи только второстепенные связки и вводные обороты, чтобы уложиться ровно в 3950-4000 знаков.
4. Сохрани жирный шрифт (**) для заголовков и чисел рейтинга.
"""
        for comp_attempt in range(1, 3):
            try:
                comp_resp = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=f"Сократи этот готовый тревел-гайд строго до 3950-4000 символов, сохранив максимум фактуры и деталей:\n\n{text}",
                    config={
                        "system_instruction": compress_instruction,
                    },
                )
                compressed = comp_resp.text.strip()
                logging.info(f"      [AI-Guide] Размер после сжатия: {len(compressed)} знаков.")
                text = compressed
                break
            except Exception as ce:
                if "503" in str(ce) and comp_attempt < 2:
                    time.sleep(3)
                    continue
                logging.warning(f"  [AI-Guide] Сбой сжатия (оставляем исходный вариант): {ce}")
                break

    return text

def get_canonical_place_url(page) -> str:
    try:
        page.wait_for_function(
            "() => window.location.href.includes('/maps/place/')",
            timeout=3000,
        )
    except Exception:
        pass
    return page.url


def extract_card_title_debug(page) -> tuple[str, str]:
    main_h1 = page.locator('div[role="main"] h1.DUwDvf, div[role="main"] h1').first
    if main_h1.is_visible(timeout=2500):
        txt = main_h1.inner_text().strip()
        if txt and txt.lower() not in ["результаты", "results", "места", "places"]:
            return txt, "div[role='main'] h1"

    duw = page.locator("h1.DUwDvf").first
    if duw.is_visible(timeout=1500):
        txt = duw.inner_text().strip()
        if txt and txt.lower() not in ["результаты", "results", "места", "places"]:
            return txt, "h1.DUwDvf"

    page_title = page.title().strip()
    if " - Google " in page_title or " – Google " in page_title:
        clean = re.split(r" [-–] Google ", page_title)[0].strip()
        if clean and clean.lower() not in ["google карты", "google maps", "поиск", "search"]:
            return clean, "page.title()"

    return "", "none"


def close_any_open_modal(page):
    """Закрывает диалог сохранения списков, возвращая фокус на карточку."""
    page.keyboard.press("Escape")
    page.wait_for_timeout(1000)


def run_batch_processing(json_path: str = "places.json"):
    with open(json_path, "r", encoding="utf-8") as f:
        places = json.load(f)

    report_data = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR.resolve()),
            headless=False,
            viewport={"width": 1366, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--lang=ru-RU",
                "--disable-gpu",
                "--no-sandbox",
            ],
        )

        page = context.new_page()
        page.set_default_timeout(0)

        page.goto("https://www.google.com/maps?hl=ru", wait_until="commit", timeout=45000)

        logging.info("=" * 70)
        logging.info("Окно браузера открыто. Убедитесь в наличии авторизации и нажмите Enter...")
        logging.info("=" * 70)
        input("Нажмите Enter в консоли для старта обработки...")

        page.set_default_timeout(15000)

        for idx, item in enumerate(places, start=1):
            place_name = item.get("name") or item.get("query")
            list_name = item.get("list_name", "Хочу посетить")

            logging.info(f"\n========================================================")
            logging.info(f"[{idx}/{len(places)}] СТАРТ: \"{place_name}\" -> Список: \"{list_name}\"")
            logging.info(f"========================================================")

            entry_result = {
                "input_query": place_name,
                "target_list": list_name,
                "matched_name": None,
                "ai_verified": False,
                "guide_length": 0,
                "google_maps_url": None,
                "already_in_list": False,
                "failed_stage": None,
                "screenshot": None,
                "status": "PENDING",
                "message": "",
            }

            try:
                # --- [1/7] Навигация ---
                encoded_query = urllib.parse.quote(place_name)
                search_url = f"https://www.google.com/maps/search/{encoded_query}?hl=ru"
                logging.info(f"[1/7] Переход по URL: {search_url}")
                page.goto(search_url, wait_until="commit", timeout=45000)
                page.wait_for_timeout(3500)

                # --- [2/7] Выдача или прямая карточка ---
                feed = page.locator('div[role="feed"]')
                if feed.is_visible(timeout=2500):
                    logging.info("[2/7] Открыта лента выдачи. Кликаем по первой карточке...")
                    first_link = page.locator('div[role="feed"] a.hfpxzc').first

                    if first_link.is_visible(timeout=3500):
                        first_link.click(force=True)
                        page.wait_for_timeout(3000)
                    else:
                        entry_result["failed_stage"] = "FEED_EMPTY"
                        entry_result["status"] = "NOT_FOUND"
                        entry_result["message"] = "Лента результатов пуста."
                        entry_result["screenshot"] = save_debug_screenshot(page, f"err_feed_{idx}")
                        report_data.append(entry_result)
                        continue

                # --- [3/7] Извлечение заголовка карточки ---
                main_title, source = "", ""
                for attempt in range(1, 6):
                    main_title, source = extract_card_title_debug(page)
                    if main_title:
                        logging.info(f"[3/7] Заголовок найден: \"{main_title}\" ({source}, попытка {attempt})")
                        break
                    page.wait_for_timeout(1000)

                if not main_title:
                    entry_result["failed_stage"] = "TITLE_NOT_FOUND"
                    entry_result["status"] = "NOT_FOUND"
                    entry_result["message"] = "Не удалось считать заголовок места."
                    entry_result["screenshot"] = save_debug_screenshot(page, f"err_title_{idx}")
                    report_data.append(entry_result)
                    continue

                context_texts = page.locator(
                    "div[role='main'] h2, div[role='main'] span.fontBodyMedium, div[role='main'] div.fontBodyMedium"
                ).all_inner_texts()
                card_context = " | ".join([t.strip() for t in context_texts[:5] if t.strip()])

                place_url = get_canonical_place_url(page)
                entry_result["matched_name"] = main_title
                entry_result["google_maps_url"] = place_url

                # --- [4/7] Валидация соответствия объекта ---
                logging.info(f"[4/7] Валидация Gemini 3.5 Flash-Lite: '{place_name}' <---> '{main_title}'...")
                is_match, reason = verify_place_match_with_ai(place_name, main_title, card_context)
                entry_result["ai_verified"] = is_match
                logging.info(f"      Вердикт ИИ: {'СОВПАЛО' if is_match else 'ОТКЛОНЕНО'} | {reason}")

                if not is_match:
                    entry_result["failed_stage"] = "AI_MISMATCH"
                    entry_result["status"] = "SKIPPED_MISMATCH"
                    entry_result["message"] = f"ИИ отклонил: {reason}"
                    entry_result["screenshot"] = save_debug_screenshot(page, f"mismatch_{idx}")
                    report_data.append(entry_result)
                    continue

                # --- [5/7] Открытие диалога сохранения списков ---
                logging.info("[5/7] Открытие меню сохранения списков...")
                save_btn = page.locator(
                    'button[data-value*="Сохран"], '
                    'button[aria-label*="Сохран"], '
                    'button[data-tooltip*="Сохран"], '
                    'button[data-value*="Save"], '
                    'button[aria-label*="Save"], '
                    'div[role="main"] button:has([data-icon="bookmark"]), '
                    'div[role="main"] button:has(span:has-text("Сохранить")), '
                    'div[role="main"] button:has(span:has-text("Сохранено")), '
                    'div[role="main"] button:has(span:has-text("Saved"))'
                ).first

                save_btn.wait_for(state="attached", timeout=8000)
                save_btn.scroll_into_view_if_needed()
                save_btn.click(force=True)
                page.wait_for_timeout(2500)

                # --- [6/7] Точный поиск строки списка и проверка aria-checked ---
                logging.info(f"[6/7] Точный поиск списка \"{list_name}\"...")

                target_text_locator = page.get_by_text(list_name, exact=True).first

                found = False
                for _ in range(8):
                    if target_text_locator.is_visible():
                        found = True
                        break
                    page.wait_for_timeout(500)

                if found:
                    menu_item = target_text_locator.locator(
                        "xpath=ancestor::div[@role='menuitemcheckbox' or @role='menuitemradio' or contains(@class, 'oMFyCd')][1]"
                    )
                    if not menu_item.is_visible():
                        menu_item = target_text_locator

                    aria_val = menu_item.get_attribute("aria-checked")
                    is_checked = (aria_val == "true")

                    if is_checked:
                        entry_result["already_in_list"] = True
                        logging.info(f"      Объект ДЕЙСТВИТЕЛЬНО уже в списке \"{list_name}\" (aria-checked=true). Повторный клик НЕ делаем.")
                    else:
                        logging.info(f"      Объект НЕ в списке \"{list_name}\" (aria-checked={aria_val}). Ставим галочку...")
                        menu_item.click(force=True)
                        page.wait_for_timeout(2000)
                else:
                    logging.info(f"      Список \"{list_name}\" не найден. Нажимаем 'Новый список'...")
                    new_btn = page.locator(
                        'button:has-text("Новый список"), '
                        'button:has-text("New list"), '
                        'div:has-text("Новый список")[role="button"], '
                        'div:has-text("New list")[role="button"]'
                    ).first

                    if new_btn.is_visible(timeout=2500):
                        new_btn.click(force=True)
                        page.wait_for_timeout(1000)
                        input_box = page.locator('input[type="text"]').last
                        input_box.fill(list_name)
                        page.wait_for_timeout(500)
                        create_btn = page.locator(
                            'button:has-text("Создать"), button:has-text("Create")'
                        ).last
                        create_btn.click(force=True)
                        logging.info(f"      Список \"{list_name}\" создан и объект привязан.")
                        page.wait_for_timeout(3000)
                    else:
                        entry_result["failed_stage"] = "LIST_CREATION_FAILED"
                        entry_result["status"] = "LIST_NOT_FOUND"
                        entry_result["message"] = f"Не удалось найти или создать список '{list_name}'."
                        entry_result["screenshot"] = save_debug_screenshot(page, f"err_list_{idx}")
                        close_any_open_modal(page)
                        report_data.append(entry_result)
                        continue

                # Закрываем модальное окно списков перед работой с заметкой
                close_any_open_modal(page)

                # --- [7/7] Проверка существующей заметки и генерация travel-guide-skill ---
                logging.info("[7/7] Проверка поля заметки на карточке...")

                card_note_area = page.locator('div[role="main"] textarea').first

                # 1. Если textarea свернута — раскрываем кликом по блоку сохранения
                if not card_note_area.is_visible(timeout=1500):
                    saved_bar = page.locator(
                        'div[role="main"] div:has-text("Сохранено в"), '
                        'div[role="main"] button:has-text("Сохранено"), '
                        'div[role="main"] div:has-text("Saved in")'
                    ).first
                    if saved_bar.is_visible(timeout=2000):
                        saved_bar.click(force=True)
                        page.wait_for_timeout(1000)

                # 2. Ожидаем появления textarea
                for _ in range(8):
                    card_note_area = page.locator('div[role="main"] textarea, textarea').first
                    if card_note_area.is_visible():
                        break
                    page.wait_for_timeout(500)

                note_saved = False
                if card_note_area.is_visible(timeout=3000):
                    card_note_area.scroll_into_view_if_needed()
                    current_val = card_note_area.input_value().strip()

                    # Если заметка уже содержит развернутый гайд — не перезаписываем
                    if len(current_val) > 200:
                        logging.info(f"      Заметка уже заполнена ({len(current_val)} знаков). Пропускаем генерацию.")
                        note_saved = True
                    else:
                        logging.info(f"      Генерация описания из {GUIDE_PROMPT_FILE} для '{main_title}'...")
                        guide_text = generate_travel_guide_note(place_name, main_title, card_context)
                        entry_result["guide_length"] = len(guide_text)

                        if guide_text:
                            logging.info(f"      Гайд сформирован (символов: {len(guide_text)}). Вставляем в форму...")
                            card_note_area.click(force=True)
                            card_note_area.fill(guide_text)

                            # Снимаем фокус кликом в заголовок карточки (триггер автосохранения по blur)
                            page.locator('div[role="main"] h1').first.click(force=True)
                            page.wait_for_timeout(2500)
                            note_saved = True
                            logging.info("      [+] Автосохранение заметки зафиксировано.")
                        else:
                            logging.warning("      [!] Гайд не был сформирован.")
                else:
                    shot_path = save_debug_screenshot(page, f"note_field_missing_{idx}")
                    logging.warning(f"      [!] Поле ввода заметки не найдено. Скриншот: {shot_path}")

                prefix_status = "Уже в списке" if entry_result["already_in_list"] else "Успешно сохранено"
                entry_result["status"] = "SUCCESS"
                entry_result["message"] = (
                    f"{prefix_status}" + (" с гайдом в заметке." if note_saved else " (без заметки).")
                )
                logging.info(f"ИТОГ: {entry_result['message']}")
                report_data.append(entry_result)

            except Exception as err:
                entry_result["failed_stage"] = "RUNTIME_EXCEPTION"
                entry_result["status"] = "RUNTIME_ERROR"
                entry_result["message"] = str(err)
                entry_result["screenshot"] = save_debug_screenshot(page, f"crash_{idx}")
                logging.error(f"  [ERROR] Сбой: {err} (Скриншот: {entry_result['screenshot']})")
                report_data.append(entry_result)

        with open(REPORT_FILE, "w", encoding="utf-8") as rf:
            json.dump(report_data, rf, ensure_ascii=False, indent=2)

        logging.info("\n" + "=" * 70)
        logging.info("ОБРАБОТКА ЗАВЕРШЕНА.")
        logging.info(f"Итоговый JSON-отчёт: {REPORT_FILE.resolve()}")
        logging.info(f"Полный журнал действий: {LOG_FILE.resolve()}")
        logging.info("=" * 70)

        context.close()


if __name__ == "__main__":
    run_batch_processing("places.json")
