import sys
from pathlib import Path

# Добавляем корень проекта в путь поиска Python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.parse
from playwright.sync_api import sync_playwright

TRIP_PROFILE_DIR = Path(__file__).resolve().parent.parent / "trip_profile"

def scrape_trip_attractions(region_query: str, list_name: str = "Хочу посетить", limit: int = 300, stop_callback=None) -> list:
    print(f"=== Поиск объектов для '{region_query}' (целевой лимит: {limit}) ===")
    
    results = []
    
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(TRIP_PROFILE_DIR.resolve()),
            headless=False,
            viewport={"width": 1366, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--lang=en-GB",
                "--window-position=-2400,-2400"
            ]
        )
        
        page = context.pages[0] if context.pages else context.new_page()

        try:
            if stop_callback and stop_callback():
                print("\n[!] Сбор остановлен пользователем.")
                return results

            # 1. Поиск региона на Trip.com
            encoded_query = urllib.parse.quote(region_query)
            search_url = f"https://uk.trip.com/global-gssearch/searchlist/search?keyword={encoded_query}&locale=en-GB&curr=RUB"
            
            print(f"Открытие поиска: {search_url}")
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3500)

            if stop_callback and stop_callback():
                print("\n[!] Сбор остановлен пользователем.")
                return results

            if "verification" in page.url or page.locator('iframe[src*="captcha"]').count() > 0:
                print("\n[!] ВНИМАНИЕ: Обнаружена капча Trip.com.")
                input("Решите ее вручную в браузере и нажмите Enter здесь...")
                page.wait_for_timeout(2000)

            try:
                cookie_btn = page.locator('button:has-text("Accept"), .ibu-cookie-banner button, div[role="dialog"] button').first
                if cookie_btn.is_visible(timeout=2000):
                    cookie_btn.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

            # 2. Поиск региона
            destinations = []
            seen_hrefs = set()

            cards = page.locator('a[href*="/travel-guide/"]').all()
            for card in cards:
                try:
                    text = card.inner_text().strip()
                    href = card.get_attribute("href")
                    if text and href:
                        clean_href = href.split('?')[0]
                        if clean_href.rstrip('/') in ["https://uk.trip.com/travel-guide", "/travel-guide"]:
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
                raise Exception(f"Не удалось найти регион '{region_query}'.")

            selected_dest = None
            query_lower = region_query.strip().lower()
            for dest in destinations:
                if dest['name'].strip().lower() == query_lower:
                    selected_dest = dest
                    break

            if selected_dest:
                print(f"Выбран регион: {selected_dest['name']} ({selected_dest['href']})")
            else:
                selected_dest = destinations[0]
                print(f"Выбран первый подходящий регион: {selected_dest['name']}")

            # 3. Переход к списку
            base_href = selected_dest['href']
            if "/destination/" in base_href:
                base_href = base_href.replace("/destination/", "/attraction/")

            if not base_href.startswith("http"):
                target_attractions_url = f"https://uk.trip.com{base_href.rstrip('/')}/tourist-attractions/"
            else:
                target_attractions_url = f"{base_href.rstrip('/')}/tourist-attractions/"

            print(f"Переход к достопримечательностям: {target_attractions_url}")
            page.goto(target_attractions_url, wait_until="domcontentloaded", timeout=30000)
            print("Ожидание прорисовки списка достопримечательностей...")
            page.wait_for_timeout(4500)

            # 4. Скролл и сбор
            seen_hrefs = set()
            max_scrolls = 600
            stuck_count = 0
            reached_limit = False

            try:
                page.locator('#POI_LIST_BOX').hover(timeout=2000)
            except Exception:
                pass

            for scroll_step in range(max_scrolls):
                if stop_callback and stop_callback():
                    print("\n[!] Остановка сбора по запросу пользователя.")
                    break

                poi_cards = page.locator('div.PoiCell_outerV2_pc__ZOyTY, div[class*="PoiCell"], div[class*="poilist-item"]').all()

                for card in poi_cards:
                    if stop_callback and stop_callback():
                        break

                    try:
                        link_el = card.locator('a[href*="/travel-guide/attraction/"], a[href]').first
                        if link_el.count() == 0:
                            continue

                        href = link_el.get_attribute("href")
                        clean_href = href.split('?')[0] if href else ""

                        title_el = card.locator('h2.Title_titleText_pc__rCDPs, h2').first
                        if title_el.count() > 0:
                            txt = title_el.inner_text().strip()
                        else:
                            txt = link_el.get_attribute("title") or ""

                        if txt and clean_href and len(txt) > 2:
                            txt = txt.split('\n')[0].strip()
                            
                            if clean_href not in seen_hrefs:
                                seen_hrefs.add(clean_href)
                                new_item = {
                                    "id": len(results) + 1,
                                    "name": txt,
                                    "rating": "-",
                                    "rating_comment": "",
                                    "list_name": list_name,
                                    "status": "В очереди",
                                    "error": ""
                                }
                                results.append(new_item)
                                print(f"  [{new_item['id']}] Attraction: {txt}")

                                if len(results) >= limit:
                                    reached_limit = True
                                    break
                    except Exception:
                        continue
                
                if reached_limit or (stop_callback and stop_callback()):
                    break

                prev_height = page.evaluate("document.body.scrollHeight")
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(2000)
                new_height = page.evaluate("document.body.scrollHeight")
                
                if new_height == prev_height:
                    stuck_count += 1
                    page.mouse.wheel(0, -600)
                    page.wait_for_timeout(800)
                    page.mouse.wheel(0, 2200)
                    page.wait_for_timeout(1800)

                    if stuck_count >= 15:
                        print("Список объектов на странице полностью исчерпан.")
                        break
                else:
                    stuck_count = 0

                if scroll_step > 0 and scroll_step % 10 == 0:
                    print(f"--- Прогресс: собрано {len(results)} объектов... ---")

        except Exception as e:
            print(f"Ошибка при сборе: {e}")
        finally:
            context.close()

    print(f"\nВсего собрано в память: {len(results)} объектов.")
    return results

if __name__ == "__main__":
    region = input("Введите регион: ").strip()
    limit_count = int(input("Лимит (300): ") or "300")
    target_list = input("Список (Хочу посетить): ").strip() or "Хочу посетить"
    items = scrape_trip_attractions(region, list_name=target_list, limit=limit_count)