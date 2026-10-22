import sys
from pathlib import Path

# Добавляем корень проекта в путь поиска Python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import urllib.parse
from playwright.sync_api import sync_playwright
from config import PLACES_FILE

# Папка для сохранения сессии и кук Trip.com
TRIP_PROFILE_DIR = Path(__file__).resolve().parent.parent / "trip_profile"

def scrape_trip_attractions(region_query: str, list_name: str = "Хочу посетить", limit: int = 10) -> list:
    print(f"=== Поиск чистых достопримечательностей для '{region_query}' (лимит: {limit}) ===")
    
    results = []
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(TRIP_PROFILE_DIR.resolve()),
            headless=False,
            viewport={"width": 1366, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--lang=en-GB"
            ]
        )
        
        page = context.pages[0] if context.pages else context.new_page()

        try:
            # 1. Переходим на глобальный поиск Trip.com
            encoded_query = urllib.parse.quote(region_query)
            search_url = f"https://uk.trip.com/global-gssearch/searchlist/search?keyword={encoded_query}&locale=en-GB&curr=RUB"
            
            print(f"Открытие поиска: {search_url}")
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)

            # Проверка на капчу
            if "verification" in page.url or page.locator('iframe[src*="captcha"]').count() > 0:
                print("\n[!] ВНИМАНИЕ: Обнаружена капча Trip.com.")
                input("Решите ее вручную в браузере и нажмите Enter здесь...")
                page.wait_for_timeout(3000)

            # Закрываем cookie-баннер
            try:
                cookie_btn = page.locator('button:has-text("Accept"), .ibu-cookie-banner button, div[role="dialog"] button').first
                if cookie_btn.is_visible(timeout=3000):
                    cookie_btn.click()
                    page.wait_for_timeout(1000)
            except Exception:
                pass

            # 2. Собираем ссылки из блока Destinations и исключаем мусор
            destinations = []
            seen_hrefs = set()

            cards = page.locator('a[href*="/travel-guide/"]').all()

            for card in cards:
                try:
                    text = card.inner_text().strip()
                    href = card.get_attribute("href")
                    
                    if text and href:
                        clean_href = href.split('?')[0]
                        
                        if clean_href.rstrip('/') == "https://uk.trip.com/travel-guide" or clean_href.rstrip('/') == "/travel-guide":
                            continue
                        if text.lower() == "travel guides":
                            continue

                        if "/travel-guide/" in clean_href and len(text) > 1:
                            if clean_href not in seen_hrefs:
                                seen_hrefs.add(clean_href)
                                destinations.append({"name": text, "href": clean_href})
                except Exception:
                    continue

            destinations = destinations[:6]

            if not destinations:
                raise Exception(f"Не удалось найти регионы в блоке Destinations для '{region_query}'.")

            print(f"\nНайдены регионы в блоке Destinations:")
            for idx, dest in enumerate(destinations, 1):
                print(f"  [{idx}] {dest['name']} ({dest['href']})")

            choice_idx = 0
            if len(destinations) > 1:
                user_input = input(f"\nВыберите номер правильного варианта (1-{len(destinations)}): ").strip()
                if user_input.isdigit() and 1 <= int(user_input) <= len(destinations):
                    choice_idx = int(user_input) - 1
                else:
                    print("Введен некорректный номер, берем первый вариант по умолчанию.")

            selected_dest = destinations[choice_idx]
            print(f"Вы выбрали: {selected_dest['name']}")

            # 3. Корректируем URL для перехода на attractions
            base_href = selected_dest['href']
            if "/destination/" in base_href:
                base_href = base_href.replace("/destination/", "/attraction/")

            if not base_href.startswith("http"):
                target_attractions_url = f"https://uk.trip.com{base_href.rstrip('/')}/tourist-attractions/"
            else:
                target_attractions_url = f"{base_href.rstrip('/')}/tourist-attractions/"

            print(f"Переход на целевой URL: {target_attractions_url}")
            page.goto(target_attractions_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)

            # 4. Накопительный глубокий скролл с прицельным извлечением названий из заголовков карточек
            collected_names = []
            seen_names = set()
            max_scrolls = 400
            scroll_pause = 2500
            stuck_count = 0
            
            for scroll_step in range(max_scrolls):
                # Ищем блоки карточек на странице
                cards = page.locator('div[class*="poilist-item"], div.poi-card, li.poi-item, div[class*="card"]').all()
                if not cards:
                    cards = page.locator('a[href*="/travel-guide/attraction/"]').all()

                for card in cards:
                    try:
                        # Прицельно выцепляем текст только из тегов заголовков внутри карточки
                        title_el = card.locator('h2, h3, .title, a.poilist-card-title').first
                        if title_el.count() > 0:
                            txt = title_el.inner_text().strip()
                        else:
                            txt = card.inner_text().strip()

                        if "\n" in txt:
                            lines = [l.strip() for l in txt.split("\n") if l.strip()]
                            txt = lines[0] if lines else txt

                        if txt and len(txt) > 2:
                            txt_lower = txt.lower()
                            
                            category_check = [
                                "sightseeing", "night view", "boat tour", "exhibitions", "concerts",
                                "popular restaurants", "popular destinations", "recommended attractions",
                                "more recommendations", "popular types"
                            ]
                            if any(cat == txt_lower for cat in category_check) or txt_lower.startswith(("london |", "rome |", "paris |")):
                                continue

                            if txt not in seen_names:
                                seen_names.add(txt)
                                collected_names.append(txt)
                    except Exception:
                        continue
                
                if len(collected_names) >= limit:
                    print(f"Достигнут целевой лимит в {limit} объектов.")
                    break

                prev_height = page.evaluate("document.body.scrollHeight")
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(scroll_pause)
                new_height = page.evaluate("document.body.scrollHeight")
                
                if new_height == prev_height:
                    stuck_count += 1
                    if stuck_count >= 8:
                        print("Достигнут абсолютный предел списка на стороне сайта.")
                        break
                    else:
                        page.mouse.wheel(0, -1000)
                        page.wait_for_timeout(1500)
                        page.mouse.wheel(0, 3000)
                        page.wait_for_timeout(3000)
                else:
                    stuck_count = 0

                if scroll_step % 20 == 0:
                    print(f"Собрано уникальных объектов на текущий момент: {len(collected_names)}...")

            final_names = collected_names[:limit]
            
            for idx, name in enumerate(final_names, 1):
                results.append({
                    "id": idx,
                    "name": name,
                    "list_name": list_name,
                    "status": "В очереди",
                    "error": ""
                })
                print(f"  [{idx}] Attraction: {name}")

        except Exception as e:
            print(f"Ошибка при парсинге достопримечательностей Trip.com: {e}")
        finally:
            context.close()

    if results:
        with open(PLACES_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"Успешно сохранено {len(results)} чистых достопримечательностей в {PLACES_FILE}")
    else:
        print("Не удалось собрать достопримечательности.")

    return results

if __name__ == "__main__":
    region = input("Введите регион на английском (например, Hohhot, Kazan, Paris): ").strip()
    limit_count = int(input("Сколько объектов собрать (по умолчанию 10): ") or "10")
    target_list = input("В какой список Google Maps сохранять (по умолчанию 'Хочу посетить'): ").strip() or "Хочу посетить"
    
    scrape_trip_attractions(region, list_name=target_list, limit=limit_count)