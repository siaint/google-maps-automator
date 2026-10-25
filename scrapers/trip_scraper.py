import sys
from pathlib import Path
import re
import json

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
            page.wait_for_timeout(3000)

            # 4. Сбор ссылок и названий из списка со скроллом
            raw_attractions = []
            max_scrolls = 600
            stuck_count = 0

            for scroll_step in range(max_scrolls):
                if stop_callback and stop_callback():
                    print("\n[!] Остановка сбора по запросу пользователя.")
                    break

                poi_cards = page.locator('div.PoiCell_outerV2_pc__ZOyTY, div[class*="PoiCell"], div[class*="poilist-item"]').all()

                for card in poi_cards:
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
                            if not any(x['href'] == clean_href for x in raw_attractions):
                                raw_attractions.append({"name": txt, "href": clean_href})
                                if len(raw_attractions) >= limit:
                                    break
                    except Exception:
                        continue
                
                if len(raw_attractions) >= limit:
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

            print(f"\nСобрано ссылок из списка: {len(raw_attractions)}. Начинаем парсинг детальных карточек...")

            # 5. Проваливаемся в каждую карточку для получения оригинального названия, превью, координат и адреса
            for idx, item in enumerate(raw_attractions, 1):
                if stop_callback and stop_callback():
                    print("\n[!] Остановка сбора по запросу пользователя.")
                    break

                en_name = item["name"]
                detail_url = item["href"]
                if not detail_url.startswith("http"):
                    detail_url = f"https://uk.trip.com{detail_url}"

                orig_name = ""
                img_url = ""
                lat = 0.0
                lon = 0.0
                address_text = ""

                try:
                    page.goto(detail_url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(1200)

                    # Извлечение координат и сырого адреса
                    geo_info = page.evaluate(r"""() => {
                        let res = { lat: 0.0, lon: 0.0, address: "" };
                        try {
                            const scripts = document.querySelectorAll('script[type="application/ld+json"], script#__NEXT_DATA__');
                            for (let s of scripts) {
                                try {
                                    let data = JSON.parse(s.innerText);
                                    let geo = data.geo || (data.props && data.props.pageProps && data.props.pageProps.poiInfo);
                                    if (geo) {
                                        if (geo.latitude && geo.longitude) {
                                            res.lat = parseFloat(geo.latitude);
                                            res.lon = parseFloat(geo.longitude);
                                        }
                                        if (geo.address) {
                                            res.address = typeof geo.address === 'string' ? geo.address : (geo.address.streetAddress || '');
                                        }
                                    }
                                } catch(e){}
                            }
                        } catch(e){}

                        if (!res.lat && window.__INITIAL_STATE__) {
                            try {
                                let stateStr = JSON.stringify(window.__INITIAL_STATE__);
                                let mLat = stateStr.match(/"lat(?:itude)?":\s*([0-9]+\.[0-9]+)/i);
                                let mLon = stateStr.match(/"lon(?:gitude)?":\s*([0-9]+\.[0-9]+)/i);
                                if (mLat && mLon) {
                                    res.lat = parseFloat(mLat[1]);
                                    res.lon = parseFloat(mLon[1]);
                                }
                            } catch(e){}
                        }

                        if (!res.lat) {
                            const mapLinks = document.querySelectorAll('a[href*="maps"], a[href*="coord"], div[data-lat]');
                            for (let a of mapLinks) {
                                let href = a.getAttribute('href') || a.getAttribute('data-lat') || '';
                                let m = href.match(/([0-9]+\.[0-9]{3,})[,_]([0-9]+\.[0-9]{3,})/);
                                if (m) {
                                    res.lat = parseFloat(m[1]);
                                    res.lon = parseFloat(m[2]);
                                    break;
                                }
                            }
                        }

                        // Поиск адреса в DOM с удалением кнопок карт и иконок
                        if (!res.address) {
                            const addrEl = document.querySelector('div[class*="address"], span[class*="Address"], div[class*="location"]');
                            if (addrEl) {
                                const clone = addrEl.cloneNode(true);
                                clone.querySelectorAll('button, a, i, svg, [class*="map"], [class*="icon"]').forEach(el => el.remove());
                                res.address = clone.innerText || clone.textContent || "";
                            }
                        }

                        return res;
                    }""")

                    if geo_info:
                        lat = float(geo_info.get("lat") or 0.0)
                        lon = float(geo_info.get("lon") or 0.0)
                        raw_addr = str(geo_info.get("address") or "").strip()
                        
                        # Чистовая очистка адреса в Python
                        raw_addr = re.sub(r'^Address[:\s]*', '', raw_addr, flags=re.IGNORECASE)
                        raw_addr = re.sub(r'[\r\n\t]+', ' ', raw_addr)
                        raw_addr = re.sub(r'[\s|·•]*Map\b.*$', '', raw_addr, flags=re.IGNORECASE)
                        address_text = re.sub(r'[\uE000-\uF8FF\uFFF0-\uFFFF\u25A0-\u25FF]', '', raw_addr).strip()

                    # Ищем оригинальное название
                    orig_el = page.locator('h1 + div, div[class*="subTitle"], span[class*="local"], h2[class*="sub"]').first
                    if orig_el.count() > 0:
                        candidate = orig_el.inner_text().strip().split('\n')[0]
                        if candidate and candidate != en_name:
                            orig_name = candidate

                    if not orig_name:
                        headers = page.locator('h1, h2, span').all()
                        for h in headers:
                            htext = h.inner_text().strip()
                            if htext and htext != en_name and any('\u4e00' <= char <= '\u9fff' for char in htext):
                                if len(htext) < 30:
                                    orig_name = htext
                                    break

                    # Поиск превью
                    for img_sel in [
                        'div[class*="Banner"] img', 
                        'div[class*="gallery"] img', 
                        'div[class*="photo"] img', 
                        'img[class*="banner"]', 
                        'div.banner-image img',
                        '.view-gallery img',
                        'img'
                    ]:
                        img_el = page.locator(img_sel).first
                        if img_el.count() > 0:
                            candidate_url = (
                                img_el.get_attribute("src") or 
                                img_el.get_attribute("data-src") or 
                                img_el.get_attribute("data-original") or ""
                            )
                            if candidate_url and candidate_url.startswith("http"):
                                img_url = candidate_url
                                break
                except Exception as e:
                    print(f"  [!] Ошибка при парсинге карточки {en_name}: {e}")

                new_item = {
                    "id": len(results) + 1,
                    "name": en_name,
                    "original_name": orig_name,
                    "url": detail_url,
                    "image": img_url,
                    "lat": lat,
                    "lon": lon,
                    "address": address_text,
                    "search_region": region_query,
                    "rating": "-",
                    "rating_comment": "",
                    "list_name": list_name,
                    "status": "В очереди",
                    "error": ""
                }
                results.append(new_item)
                
                coord_mark = f" 📍 [{lat:.4f}, {lon:.4f}]" if lat and lon else ""
                display_str = f"{en_name} ({orig_name})" if orig_name else en_name
                print(f"  [{new_item['id']}/{len(raw_attractions)}] Parsed: {display_str}{coord_mark}")

        except Exception as e:
            print(f"Ошибка при сборе: {e}")
        finally:
            context.close()

    print(f"\nВсего успешно обработано: {len(results)} объектов.")
    return results

if __name__ == "__main__":
    region = input("Введите регион: ").strip()
    limit_count = int(input("Лимит (300): ") or "300")
    target_list = input("Список (Хочу посетить): ").strip() or "Хочу посетить"
    items = scrape_trip_attractions(region, list_name=target_list, limit=limit_count)