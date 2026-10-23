import logging
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
        """Гарантированно закрывает окно браузера."""
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
        """Мгновенно прерывает работу и закрывает браузер."""
        self.is_cancelled = True
        self.stop()

    def _wait_cancelled(self, timeout_ms: int) -> bool:
        """Ждет заданное время, но каждые 100мс проверяет флаг отмены."""
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

        try:
            page_title = self.page.title().strip()
            if " - Google " in page_title or " – Google " in page_title:
                clean = re.split(r" [-–] Google ", page_title)[0].strip()
                if clean and clean.lower() not in ["google карты", "google maps", "поиск"]:
                    return clean
        except Exception:
            pass
        return ""

    def process_item(self, place_data, status_cb=None) -> tuple[bool, str]:
        """
        Принимает либо словарь объекта (содержащий name, original_name, search_region, list_name),
        либо по старинке строку place_name (для обратной совместимости).
        """
        if isinstance(place_data, dict):
            place_name = place_data.get("name", "")
            original_name = place_data.get("original_name", "")
            search_region = place_data.get("search_region", "")
            list_name = place_data.get("list_name", "Хочу посетить")
        else:
            place_name = str(place_data)
            original_name = ""
            search_region = ""
            list_name = "Хочу посетить"

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

            # 1. Формируем поисковый запрос (приоритет у оригинального названия + региона)
            query_parts = []
            if original_name:
                query_parts.append(original_name)
            query_parts.append(place_name)
            if search_region:
                query_parts.append(search_region)
            
            full_query = " ".join(query_parts)

            report(f"1/7: Поиск на карте: '{full_query}'...")
            encoded = urllib.parse.quote(full_query)
            page.goto(f"https://www.google.com/maps/search/{encoded}?hl=ru", wait_until="commit", timeout=30000)
            
            if self._wait_cancelled(3000):
                return False, "Прервано пользователем"

            # 2. Выдача или карточка
            feed = page.locator('div[role="feed"]')
            if feed.is_visible(timeout=2500):
                report("2/7: Выбор первого объекта из поисковой выдачи...")
                first_link = page.locator('div[role="feed"] a.hfpxzc').first
                if first_link.is_visible(timeout=3000):
                    first_link.click(force=True)
                    if self._wait_cancelled(2500):
                        return False, "Прервано пользователем"
                else:
                    return False, "Лента выдачи пуста (объект не найден)"
            else:
                report("2/7: Объект открыт сразу без промежуточного списка...")

            if self.is_cancelled:
                return False, "Прервано пользователем"

            # 3. Заголовок карточки
            report("3/7: Чтение карточки...")
            title = ""
            for _ in range(8):
                if self.is_cancelled:
                    return False, "Прервано пользователем"
                title = self._extract_title()
                if title:
                    break
                if self._wait_cancelled(800):
                    return False, "Прервано пользователем"

            if not title:
                return False, "Не удалось определить заголовок карточки"

            card_panel = self._get_card_panel()
            context_texts = card_panel.locator("h2, span.fontBodyMedium").all_inner_texts()
            card_context = " | ".join([t.strip() for t in context_texts[:5] if t.strip()])

            # 4. Валидация ИИ (проверяем по основному имени)
            is_match, reason = ai_service.verify_place_match(place_name, title, card_context, status_cb=report)
            if not is_match:
                return False, f"ИИ отклонил: {reason}"

            if self.is_cancelled:
                return False, "Прервано пользователем"

            # 5. Открытие меню сохранения
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

            # 6. Поиск списка и установка чекбокса
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
                is_checked = (aria_val == "true")

                if is_checked:
                    report(f"Объект уже отмечен в списке \"{list_name}\". Ничего не нажимаем.")
                else:
                    report(f"Ставим галочку в списке \"{list_name}\"...")
                    menu_item.click(force=True)
                    if self._wait_cancelled(2000):
                        return False, "Прервано пользователем"
            else:
                page.keyboard.press("Escape")
                return False, f"Список \"{list_name}\" не найден в окне"

            page.keyboard.press("Escape")
            if self._wait_cancelled(1200):
                return False, "Прервано пользователем"

            # 7. Работа с заметкой
            report("7/7: Проверка заметки на карточке...")
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

            existing_note = ""
            for nb in card_panel.locator('div[role="button"][tabindex="0"], div.fontBodyMedium').all():
                ntxt = nb.inner_text().strip()
                if len(ntxt) > 100 and ("[" in ntxt or "5A" in ntxt or "Рейтинг" in ntxt):
                    existing_note = ntxt
                    break

            if len(existing_note) > 100:
                report("Заметка уже заполнена ранее.")
                return True, f"Уже сохранён в '{list_name}' с готовой заметкой"

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
                return False, "Не удалось открыть поле заметки на карточке (см. debug_maps_card.png)"

            if len(card_note_area.input_value().strip()) > 100:
                report("Заметка уже заполнена ранее.")
                return True, f"Уже сохранён в '{list_name}' с готовой заметкой"

            if self.is_cancelled:
                return False, "Прервано пользователем"

            # Генерация гайда через Gemini
            guide_text, err = ai_service.generate_guide(
                place_name, title, card_context, 
                status_cb=report, 
                check_cancelled=lambda: self.is_cancelled
            )
            if err:
                return False, f"Ошибка скилла: {err}"

            if self.is_cancelled:
                return False, "Прервано пользователем"

            # Вставка и сохранение
            report(f"7/7: Вставка заметки ({len(guide_text)} зн.)...")
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
            print(f">>> [EXC] Исключение: {e}", flush=True)
            return False, f"Исключение браузера: {e}"

        finally:
            report("Закрытие браузера...")
            self.stop()