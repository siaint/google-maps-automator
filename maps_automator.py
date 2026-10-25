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
                    "--lang=ru-RU",
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

    def _validate_current_card(self, place_name: str, original_name: str, address: str, lat: float, lon: float, status_cb=None) -> tuple[bool, str]:
        """Проверяет открытую карточку по координатам (расстоянию) и через ИИ-валидатор."""
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

        # Извлекаем координаты из текущего URL Google Maps (@lat,lon)
        g_lat, g_lon = 0.0, 0.0
        match_coords = re.search(r'@([0-9]+\.[0-9]+),([0-9]+\.[0-9]+)', self.page.url)
        if match_coords:
            g_lat = float(match_coords.group(1))
            g_lon = float(match_coords.group(2))

        dist = 0.0
        if lat and lon and g_lat and g_lon:
            dist = self._haversine(lat, lon, g_lat, g_lon)
            # Если расстояние больше 150 км — это гарантированно не тот объект (одноименная тезка в другом городе)
            if dist > 150.0:
                return False, f"Слишком далеко от исходных координат ({dist:.1f} км)"

        card_panel = self._get_card_panel()
        context_texts = card_panel.locator("h2, span.fontBodyMedium").all_inner_texts()
        base_context = " | ".join([t.strip() for t in context_texts[:5] if t.strip()])

        # Формируем расширенный контекст для ИИ с учетом адреса и дистанции
        context_parts = [base_context]
        if address:
            context_parts.append(f"Ожидаемый адрес: {address}")
        if lat and lon and g_lat and g_lon:
            context_parts.append(f"Расстояние до исходной точки Trip.com: {dist:.1f} км")
        card_context = " | ".join(context_parts)

        # Передаем на финальную валидацию ИИ
        is_match, reason = ai_service.verify_place_match(place_name, title, card_context, status_cb=report)
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
        else:
            place_name = str(place_data)
            original_name = ""
            search_region = ""
            address = ""
            lat, lon = 0.0, 0.0
            list_name = "Хочу посетить"
            guide_text = ""

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

            # Формируем поисковый запрос (название + адрес/регион)
            query_parts = []
            if original_name:
                query_parts.append(original_name)
            query_parts.append(place_name)
            if address:
                query_parts.append(address)
            elif search_region:
                query_parts.append(search_region)
            
            full_query = " ".join(query_parts)

            report(f"1/7: Поиск на карте: '{full_query}'...")
            encoded = urllib.parse.quote(full_query)
            page.goto(f"https://www.google.com/maps/search/{encoded}?hl=ru", wait_until="commit", timeout=30000)
            
            if self._wait_cancelled(3000):
                return False, "Прервано пользователем"

            feed = page.locator('div[role="feed"]')
            is_valid_match = False
            last_error = "Объект не найден"

            if feed.is_visible(timeout=2500):
                report("2/7: Обнаружен список выдачи. Проверяем первые варианты...")
                
                # Перебираем первые 3 результата в выдаче
                for i in range(3):
                    if self.is_cancelled:
                        return False, "Прервано пользователем"
                    
                    links = page.locator('div[role="feed"] a.hfpxzc').all()
                    if i >= len(links):
                        break
                    
                    report(f"Проверка варианта №{i + 1} из выдачи...")
                    links[i].click(force=True)
                    if self._wait_cancelled(2000):
                        return False, "Прервано пользователем"

                    is_match, reason = self._validate_current_card(place_name, original_name, address, lat, lon, status_cb=report)
                    if is_match:
                        report(f"4/7: Вариант №{i + 1} успешно прошел проверку!")
                        is_valid_match = True
                        break
                    else:
                        report(f"Вариант №{i + 1} не подошел: {reason}. Возврат к списку...")
                        last_error = reason
                        page.go_back()
                        self._wait_cancelled(1500)

                if not is_valid_match:
                    return False, f"Ни один из вариантов не подошел. Последняя ошибка: {last_error}"
            else:
                report("2/7: Объект открыт сразу без промежуточного списка...")
                if self.is_cancelled:
                    return False, "Прервано пользователем"

                is_match, reason = self._validate_current_card(place_name, original_name, address, lat, lon, status_cb=report)
                if is_match:
                    is_valid_match = True
                else:
                    return False, f"ИИ отклонил: {reason}"

            if not is_valid_match:
                return False, "Объект не прошел проверку валидации"

            if self.is_cancelled:
                return False, "Прервано пользователем"

            card_panel = self._get_card_panel()
            report("5/7: Меню сохранения...")
            save_btn = card_panel.locator(
                'button[data-value*="Сохран"], button[aria-label*="Сохран"], '
                'button[data-value*="Save"], button[aria-label*="Save"], '
                'button:has([data-icon="bookmark"])'
            ).first
            save_btn.wait_for(state="attached", timeout=6000)
            save_btn.click(force=True)
            
            if self._wait_cancelled(2000):
                return False, "Прервано пользователем"

            report(f"6/7: Поиск списка \"{list_name}\"...")
            target_text_locator = page.get_by_text(list_name, exact=True).first

            found = False
            for _ in range(8):
                if self.is_cancelled:
                    return False, "Прервано пользователем"
                if target_text_locator.is_visible():
                    found = True
                    break
                if self._wait_cancelled(500):
                    return False, "Прервано пользователем"

            if found:
                menu_item = target_text_locator.locator(
                    "xpath=ancestor::div[@role='menuitemcheckbox' or @role='menuitemradio' or contains(@class, 'oMFyCd')][1]"
                )
                if not menu_item.is_visible():
                    menu_item = target_text_locator

                aria_val = menu_item.get_attribute("aria-checked")
                if aria_val != "true":
                    menu_item.click(force=True)
                    if self._wait_cancelled(2000):
                        return False, "Прервано пользователем"
            else:
                page.keyboard.press("Escape")
                return False, f"Список \"{list_name}\" не найден в окне"

            page.keyboard.press("Escape")
            if self._wait_cancelled(1200):
                return False, "Прервано пользователем"

            # 7. Проверка существующей заметки и вставка
            report("7/7: Проверка существующей заметки...")
            card_panel = self._get_card_panel()

            accordion_header = card_panel.locator(
                'div[role="button"]:has-text("Сохранено в"), '
                'div:has-text("Сохранено в списках"), '
                'div:has-text("Сохранено в списке")'
            ).last

            if accordion_header.is_visible(timeout=2000):
                if not card_panel.locator('textarea, button:has-text("Добавить заметку")').first.is_visible():
                    accordion_header.scroll_into_view_if_needed()
                    accordion_header.click(force=True)
                    if self._wait_cancelled(1000):
                        return False, "Прервано пользователем"

            existing_textarea = card_panel.locator('textarea').first
            if existing_textarea.is_visible(timeout=1500):
                existing_text = existing_textarea.input_value().strip()
                if existing_text:
                    page.keyboard.press("Escape")
                    return False, f"Пропущено: у объекта уже есть ненужная к перезаписи заметка ({len(existing_text)} зн.)"

            note_btn = card_panel.locator(
                'button:has-text("Добавить заметку"), '
                'button[aria-label*="заметк" i], '
                'div[role="button"]:has-text("Добавить заметку"), '
                'div:has-text("Добавьте заметку")'
            ).first

            if note_btn.is_visible(timeout=1500):
                note_btn.scroll_into_view_if_needed()
                note_btn.click(force=True)
                if self._wait_cancelled(800):
                    return False, "Прервано пользователем"

            card_note_area = card_panel.locator('textarea').first
            found_ta = False
            for _ in range(8):
                if self.is_cancelled:
                    return False, "Прервано пользователем"
                if card_note_area.is_visible():
                    found_ta = True
                    break
                if self._wait_cancelled(400):
                    return False, "Прервано пользователем"

            if not found_ta:
                page.screenshot(path="debug_maps_card.png", full_page=False)
                return False, "Не удалось открыть поле заметки на карточке"

            current_textarea_text = card_note_area.input_value().strip()
            if current_textarea_text:
                page.keyboard.press("Escape")
                return False, f"Пропущено: у объекта уже есть сохраненная заметка ({len(current_textarea_text)} зн.)"

            if not guide_text:
                guide_text = f"Объект: {place_name} ({search_region})"

            report(f"7/7: Вставка новой заметки ({len(guide_text)} зн.)...")
            card_note_area.scroll_into_view_if_needed()
            card_note_area.click(force=True)
            card_note_area.fill(guide_text)
            if self._wait_cancelled(1000):
                return False, "Прервано пользователем"

            report("7/7: Сохранение заметки...")
            header = card_panel.locator('h1.DUwDvf').first
            if header.is_visible():
                header.click(force=True)
            if self._wait_cancelled(2500):
                return False, "Прервано пользователем"

            return True, f"Сохранено в '{list_name}' с заметкой ({len(guide_text)} зн.)"

        except Exception as e:
            if self.is_cancelled or "closed" in str(e).lower():
                return False, "Прервано пользователем"
            return False, f"Исключение браузера: {e}"

        finally:
            self.stop()