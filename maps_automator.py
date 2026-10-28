import difflib
import logging
import math
import re
import sys
import urllib.parse
from playwright.sync_api import sync_playwright
from config import PROFILE_DIR
import ai_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("maps_automator")


class MapsAutomator:
    def __init__(self):
        self.playwright = None
        self.context = None
        self.page = None
        self.is_cancelled = False

    def start(self):
        self.is_cancelled = False
        if not self.context:
            self.playwright = sync_playwright().start()
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR.resolve()),
                headless=False,
                viewport={"width": 1366, "height": 900},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--lang=en-US",
                    "--disable-gpu",
                    "--no-sandbox",
                ],
            )
            if len(self.context.pages) > 0:
                self.page = self.context.pages[0]
            else:
                self.page = self.context.new_page()

    def stop(self):
        if self.context:
            try:
                self.context.close()
            except Exception:
                pass
            self.context = None
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

    def cancel(self):
        self.is_cancelled = True
        self.stop()

    def _wait_cancelled(self, timeout_ms: int) -> bool:
        elapsed = 0
        while elapsed < timeout_ms:
            if self.is_cancelled:
                return True
            try:
                self.page.wait_for_timeout(100)
            except Exception:
                return True
            elapsed += 100
        return self.is_cancelled

    def _get_card_panel(self):
        h1 = self.page.locator('h1.DUwDvf').first
        if h1.is_visible():
            return h1.locator("xpath=ancestor::div[@role='main'][1]")
        return self.page.locator('div[role="main"]').last

    def _extract_title(self) -> str:
        selectors = [
            'h1.DUwDvf',
            'div[role="main"] h1.DUwDvf',
            'div.TI69fd h1',
            'div[role="main"] h1'
        ]
        for sel in selectors:
            loc = self.page.locator(sel).first
            try:
                if loc.is_visible(timeout=1000):
                    txt = loc.inner_text().strip()
                    if txt and txt.lower() not in ["результаты", "results", "места", "places"]:
                        return txt
            except Exception:
                continue
        return ""

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Вычисляет расстояние между двумя гео-точками в километрах."""
        if not lat1 or not lon1 or not lat2 or not lon2:
            return 0.0
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def _validate_current_card(self, place_name: str, original_name: str, address: str, search_region: str, lat: float, lon: float, status_cb=None) -> tuple[bool, str]:
        def report(txt):
            if status_cb:
                status_cb(txt)

        title = ""
        for _ in range(8):
            if self.is_cancelled:
                return False, "Прервано пользователем"
            title = self._extract_title()
            if title:
                break
            if self._wait_cancelled(500):
                return False, "Прервано пользователем"

        if not title:
            return False, "Не удалось определить заголовок карточки"

        g_lat, g_lon = 0.0, 0.0
        match_coords = re.search(r'@([0-9.-]+),([0-9.-]+)', self.page.url)
        if match_coords:
            g_lat = float(match_coords.group(1))
            g_lon = float(match_coords.group(2))
        else:
            match_3d4d = re.search(r'!3d([0-9.-]+)!4d([0-9.-]+)', self.page.url)
            if match_3d4d:
                g_lat = float(match_3d4d.group(1))
                g_lon = float(match_3d4d.group(2))

        dist = 0.0
        if lat and lon and g_lat and g_lon:
            dist = self._haversine(lat, lon, g_lat, g_lon)
            if dist > 150.0:
                return False, f"Слишком далеко от исходных координат ({dist:.1f} км)"

        geo_context = address if address else search_region

        is_match, reason = ai_service.verify_place_match(
            place_name=place_name,
            original_name=original_name,
            geo_context=geo_context,
            card_title=title
        )
        return is_match, reason

    def process_item(self, place_data, status_cb=None) -> tuple[bool, str]:
        if isinstance(place_data, dict):
            place_name = place_data.get("name", "")
            original_name = place_data.get("original_name", "")
            search_region = place_data.get("search_region", "")
            address = place_data.get("address", "")
            lat = float(place_data.get("lat") or 0.0)
            lon = float(place_data.get("lon") or 0.0)
            list_name = place_data.get("list_name", "Хочу посетить")
            guide_text = place_data.get("rating_comment", "").strip()
            full_query = place_data.get("search_query", "").strip()
        else:
            place_name = str(place_data)
            original_name = ""
            search_region = ""
            address = ""
            lat, lon = 0.0, 0.0
            list_name = "Хочу посетить"
            guide_text = ""
            full_query = ""

        if not full_query:
            query_parts = []
            if original_name:
                query_parts.append(original_name)
            if place_name:
                query_parts.append(place_name)
            if address:
                query_parts.append(address)
            elif search_region:
                query_parts.append(search_region)
            full_query = " ".join(query_parts)

        def report(step_text: str):
            if status_cb:
                status_cb(step_text)
            print(f">>> [{place_name}] {step_text}", flush=True)

        try:
            self.is_cancelled = False
            report("Открытие браузера...")
            self.start()
            if self.is_cancelled:
                return False, "Прервано пользователем"

            page = self.page
            encoded_base_query = urllib.parse.quote(full_query)

            report(f"1/7: Поиск на карте: '{full_query}'...")
            page.goto(f"https://www.google.com/maps/search/{encoded_base_query}?hl=en", wait_until="commit", timeout=30000)
            
            if self._wait_cancelled(2500):
                return False, "Прервано пользователем"

            feed = page.locator('div[role="feed"]')
            is_valid_match = False
            last_error = "Объект не найден"

            if feed.is_visible(timeout=2500):
                report("2/7: Обнаружен список выдачи. Сканирование карточек-кандидатов...")
                page.wait_for_timeout(400)
                
                link_locators = page.locator('div[role="feed"] a.hfpxzc').all()
                if not link_locators:
                    return False, "Объект не найден на картах (пустая выдача)"

                candidates_meta = []
                for idx, link in enumerate(link_locators[:5]):
                    try:
                        href = link.get_attribute("href")
                        card_block = link.locator("xpath=ancestor::div[contains(@class, 'bfdHYd') or contains(@class, 'i0jCxf')][1]")
                        text_content = card_block.inner_text() if card_block.count() > 0 else link.inner_text()
                        
                        card_lines = [line.strip() for line in text_content.split("\n") if line.strip()]
                        card_title_candidate = card_lines[0] if card_lines else ""

                        name_to_check = place_name.lower()
                        orig_to_check = original_name.lower() if original_name else ""
                        cand_lower = card_title_candidate.lower()

                        score_main = difflib.SequenceMatcher(None, name_to_check, cand_lower).ratio() * 100
                        score_orig = difflib.SequenceMatcher(None, orig_to_check, cand_lower).ratio() * 100 if orig_to_check else 0.0
                        name_match_score = max(score_main, score_orig)

                        if name_match_score < 40.0:
                            continue

                        rating_match = re.search(r'([0-9],[0-9]|[0-9]\.[0-9])\s*\(([0-9]+)\)', text_content)
                        rating = float(rating_match.group(1).replace(',', '.')) if rating_match else 0.0
                        reviews_count = int(rating_match.group(2)) if rating_match else 0
                        
                        has_phone = "+" in text_content
                        has_website = "website" in text_content.lower() or "веб-сайт" in text_content.lower()
                        
                        activity_score = (min(reviews_count, 1000) * 0.5) + (rating * 10)
                        if has_phone:
                            activity_score += 50
                        if has_website:
                            activity_score += 50

                        position_bonus = max(0, (5 - idx) * 5)
                        weight = name_match_score + activity_score + position_bonus

                        candidates_meta.append({
                            "index": idx,
                            "href": href,
                            "weight": weight,
                            "text": text_content.replace("\n", " | ")[:100]
                        })
                    except Exception:
                        continue

                candidates_meta.sort(key=lambda x: x["weight"], reverse=True)
                report(f"Собрано подходящих кандидатов: {len(candidates_meta)}. Проверяем по порядку...")

                for cand in candidates_meta:
                    if self.is_cancelled:
                        return False, "Прервано пользователем"

                    report(f"Проверка кандидата (вес: {cand['weight']:.1f}): {cand['text']}...")

                    if cand["href"]:
                        page.goto(cand["href"], wait_until="commit", timeout=30000)
                    else:
                        fresh_links = page.locator('div[role="feed"] a.hfpxzc').all()
                        if cand["index"] < len(fresh_links):
                            fresh_links[cand["index"]].click(force=True)
                        else:
                            continue

                    if self._wait_cancelled(1500):
                        return False, "Прервано пользователем"

                    is_match, reason = self._validate_current_card(
                        place_name, original_name, address, search_region, lat, lon, status_cb=report
                    )
                    if is_match:
                        report("4/7: Карточка успешно прошла проверку!")
                        is_valid_match = True
                        break
                    else:
                        report(f"Вариант отклонен: {reason}. Переходим к следующему...")
                        last_error = reason
                        page.goto(f"https://www.google.com/maps/search/{encoded_base_query}?hl=en", wait_until="commit", timeout=30000)
                        if self._wait_cancelled(1200):
                            return False, "Прервано пользователем"

                if not is_valid_match:
                    return False, f"Ни один из вариантов не подошел. Последняя ошибка: {last_error}"
            else:
                report("2/7: Объект открыт сразу без промежуточного списка...")
                if self.is_cancelled:
                    return False, "Прервано пользователем"

                title = self._extract_title()
                if not title:
                    return False, "Объект не найден (пустая карточка)"

                is_match, reason = self._validate_current_card(
                    place_name, original_name, address, search_region, lat, lon, status_cb=report
                )
                if is_match:
                    is_valid_match = True
                else:
                    return False, f"ИИ отклонил: {reason}"

            if not is_valid_match:
                return False, "Объект не прошел проверку валидации"

            if self.is_cancelled:
                return False, "Прервано пользователем"

            # Быстрая стабилизация карточки
            if self._wait_cancelled(1000):
                return False, "Прервано пользователем"

            card_panel = self._get_card_panel()
            report("5/7: Меню сохранения...")
            save_btn = card_panel.locator(
                'button[data-value*="Сохран"], button[aria-label*="Сохран"], '
                'button[data-value*="Save"], button[aria-label*="Save"], '
                'button:has([data-icon="bookmark"])'
            ).first
            
            save_btn.wait_for(state="visible", timeout=6000)
            save_btn.click(force=True)

            report(f"6/7: Поиск списка \"{list_name}\"...")
            target_text_locator = page.get_by_text(list_name, exact=True).first

            # Ищем пункт списка в выпадающем окне
            found = False
            for _ in range(30):
                if self.is_cancelled:
                    return False, "Прервано пользователем"
                if target_text_locator.is_visible():
                    found = True
                    break
                if self._wait_cancelled(200):
                    return False, "Прервано пользователем"

            if not found:
                page.screenshot(path="debug_maps_lists.png", full_page=False)
                page.keyboard.press("Escape")
                return False, f"Список \"{list_name}\" не найден в окне"

            # Клик точно по тексту названия списка (переключает чекбокс без подвисаний)
            # --- ШАГ 6/7: ВЫБОР СПИСКА И ОЖИДАНИЕ ФИКСАЦИИ ---
            report(f"6/7: Поиск списка \"{list_name}\"...")
            target_text_locator = page.get_by_text(list_name, exact=True).first

            found = False
            for _ in range(30):
                if self.is_cancelled:
                    return False, "Прервано пользователем"
                if target_text_locator.is_visible():
                    found = True
                    break
                if self._wait_cancelled(200):
                    return False, "Прервано пользователем"

            if not found:
                page.screenshot(path="debug_maps_lists.png", full_page=False)
                page.keyboard.press("Escape")
                return False, f"Список \"{list_name}\" не найден в окне"

            report(f"6/7: Выбор списка \"{list_name}\"...")
            target_text_locator.click(force=True)

            # Закрываем всплывающее меню списков
            if self._wait_cancelled(600):
                return False, "Прервано пользователем"
            page.keyboard.press("Escape")

            report("6/7: Ожидание подтверждения сохранения (Saved)...")
            # Ждём, пока статус "Saving..." исчезнет, а кнопка перейдёт в "Saved"
            saved_confirmed = False
            for _ in range(25):  # до 12.5 секунд на сетевой ответ
                if self.is_cancelled:
                    return False, "Прервано пользователем"

                is_saving = card_panel.locator(
                    'button:has-text("Saving..."), button:has-text("Сохранение..."), [aria-label*="Saving"]'
                ).is_visible()

                has_saved = card_panel.locator(
                    'button:has-text("Saved"), button:has-text("Сохранено"), '
                    'button[data-value*="Saved"], button[aria-label*="Saved"], '
                    'div[role="button"]:has-text("Saved in"), div[role="button"]:has-text("Сохранено в")'
                ).first.is_visible()

                if not is_saving and has_saved:
                    saved_confirmed = True
                    break

                if self._wait_cancelled(500):
                    return False, "Прервано пользователем"

            if not saved_confirmed:
                report("Предупреждение: статус 'Saved' не появился за отведённое время, пробуем продолжить...")

            # --- ШАГ 7/7: РАБОТА С ЗАМЕТКОЙ ---
            report("7/7: Поиск блока заметки...")
            card_panel = self._get_card_panel()

            # Если список свернут в аккордеон/чип — разворачиваем его
            saved_badge = card_panel.locator(
                'button:has-text("Saved in"), '
                'div[role="button"]:has-text("Saved in"), '
                'button:has-text("Сохранено в"), '
                'div[role="button"]:has-text("Сохранено в")'
            ).first

            if saved_badge.is_visible(timeout=1500):
                try:
                    saved_badge.scroll_into_view_if_needed()
                    saved_badge.click()
                    self._wait_cancelled(500)
                except Exception:
                    pass

            # Ищем поле ввода заметки или кнопку её добавления
            note_input = card_panel.locator(
                'textarea, '
                'input[placeholder*="note" i], '
                'input[placeholder*="заметк" i], '
                'div[role="textbox"]'
            ).first

            found_input = False
            for _ in range(12):
                if self.is_cancelled:
                    return False, "Прервано пользователем"
                if note_input.is_visible():
                    found_input = True
                    break

                add_note_btn = card_panel.locator(
                    'button[aria-label*="note" i], '
                    'button[aria-label*="заметк" i], '
                    'button:has-text("Add note"), '
                    'button:has-text("Add a note"), '
                    'button:has-text("Edit note"), '
                    'button:has-text("Добавить заметку"), '
                    'button:has-text("Изменить заметку"), '
                    'div[role="button"]:has-text("Add a note"), '
                    'div[role="button"]:has-text("Edit note")'
                ).first

                if add_note_btn.is_visible():
                    try:
                        add_note_btn.scroll_into_view_if_needed()
                        add_note_btn.click(force=True)
                        self._wait_cancelled(500)
                    except Exception:
                        pass

                if self._wait_cancelled(300):
                    return False, "Прервано пользователем"

            if not found_input:
                global_ta = page.locator('textarea, div[role="dialog"] textarea').first
                if global_ta.is_visible(timeout=1000):
                    note_input = global_ta
                    found_input = True

            if not found_input:
                page.screenshot(path="debug_maps_card.png", full_page=False)
                return False, "Не удалось открыть поле заметки на карточке"

            # Проверяем наличие уже существующей заметки
            try:
                current_text = note_input.input_value().strip()
                if current_text:
                    return True, f"Уже есть: у объекта есть сохраненная заметка ({len(current_text)} зн.)"
            except Exception:
                pass

            if not guide_text:
                guide_text = f"Объект: {place_name} ({search_region})"

            # Ввод текста заметки
            report(f"7/7: Вставка новой заметки ({len(guide_text)} зн.)...")
            
            # Стало: аккуратная плавная прокрутка строго внутри левой панели и клик без скачка окна
            note_input.evaluate("el => el.scrollIntoView({ block: 'nearest', inline: 'nearest' })")
            note_input.click(force=True)
            note_input.fill(guide_text)
            

            # Снимаем дамп для проверки состояния формы заметки
            report("7/7: Сохранение дампа DOM для отладки...")
            try:
                page.screenshot(path="debug_note_opened.png", full_page=False)
                with open("debug_note_panel.html", "w", encoding="utf-8") as f:
                    f.write(card_panel.inner_html())
            except Exception as e:
                report(f"Ошибка дампа: {e}")

            # Снимаем фокус с поля для запуска автосохранения
            note_input.evaluate("el => el.blur()")

            report("7/7: Ожидание отправки заметки...")
            if self._wait_cancelled(5000):
                return False, "Прервано пользователем"

            return True, f"Сохранено в '{list_name}' с заметкой ({len(guide_text)} зн.)"

        except Exception as e:
            if self.is_cancelled or "closed" in str(e).lower():
                return False, "Прервано пользователем"
            return False, f"Исключение браузера: {e}"

        finally:
            self.stop()