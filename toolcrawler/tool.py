import asyncio
import csv
import os
import re
import json
import hashlib
import random
import requests
import time
import sys

# Force stdout to use utf-8 encoding and enable line buffering (for CI/CD like Github Actions)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from playwright.async_api import async_playwright

# API Config (New PHP Worker)
API_URL = os.getenv("CRAWLER_API_URL") or "https://data.dulieuluatphap.com/api_worker.php?action=ingest"
API_TASK_CLAIM_URL = os.getenv("CRAWLER_API_TASK_CLAIM_URL") or "https://data.dulieuluatphap.com/api_worker.php?action=claim"
API_TASK_COMPLETE_URL = os.getenv("CRAWLER_API_TASK_COMPLETE_URL") or "https://data.dulieuluatphap.com/api_worker.php?action=complete"
API_KEY = os.getenv("CRAWLER_API_KEY") or "dl_secret_2026"

if not API_KEY:
    print("[!] CẢNH BÁO: Chưa cấu hình biến môi trường CRAWLER_API_KEY")


PROVINCES = [
    "Hà Nội", "Hồ Chí Minh", "Hải Phòng", "Đà Nẵng", "Cần Thơ", 
    "An Giang", "Bà Rịa - Vũng Tàu", "Bạc Liêu", "Bắc Giang", "Bắc Kạn", "Bắc Ninh", "Bến Tre", "Bình Dương", "Bình Định", "Bình Phước", "Bình Thuận", "Cà Mau", "Cao Bằng", "Đắk Lắk", "Đắk Nông", "Điện Biên", "Đồng Nai", "Đồng Tháp", "Gia Lai", "Hà Giang", "Hà Nam", "Hà Tĩnh", "Hải Dương", "Hậu Giang", "Hòa Bình", "Hưng Yên", "Khánh Hòa", "Kiên Giang", "Kon Tum", "Lai Châu", "Lâm Đồng", "Lạng Sơn", "Lào Cai", "Long An", "Nam Định", "Nghệ An", "Ninh Bình", "Ninh Thuận", "Phú Thọ", "Phú Yên", "Quảng Bình", "Quảng Nam", "Quảng Ngãi", "Quảng Ninh", "Quảng Trị", "Sóc Trăng", "Sơn La", "Tây Ninh", "Thái Bình", "Thái Nguyên", "Thanh Hóa", "Thừa Thiên Huế", "Tiền Giang", "Trà Vinh", "Tuyên Quang", "Vĩnh Long", "Vĩnh Phúc", "Yên Bái"
]

def init_db():
    pass # No longer need to init db directly from crawler


def remove_vietnamese_accents(s):
    s = re.sub(r'[àáạảãâầấậẩẫăằắặẳẵ]', 'a', s)
    s = re.sub(r'[èéẹẻẽêềếệểễ]', 'e', s)
    s = re.sub(r'[ìíịỉĩ]', 'i', s)
    s = re.sub(r'[òóọỏõôồốộổỗơờớợởỡ]', 'o', s)
    s = re.sub(r'[ùúụủũưừứựửữ]', 'u', s)
    s = re.sub(r'[ỳýỵỷỹ]', 'y', s)
    s = re.sub(r'[đ]', 'd', s)
    s = re.sub(r'[ÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴ]', 'A', s)
    s = re.sub(r'[ÈÉẸẺẼÊỀẾỆỂỄ]', 'E', s)
    s = re.sub(r'[ÌÍỊỈĨ]', 'I', s)
    s = re.sub(r'[ÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠ]', 'O', s)
    s = re.sub(r'[ÙÚỤỦŨƯỪỨỰỬỮ]', 'U', s)
    s = re.sub(r'[ỲÝỴỶỸ]', 'Y', s)
    s = re.sub(r'[Đ]', 'D', s)
    return s

def parse_vn_address(address_str, default_loc=""):
    zip_code, state_name, city_only = "", "", ""
    if not address_str:
        address_str = ""
        
    zm = re.search(r'\b(\d{5,6})\b', address_str)
    if zm:
        zip_code = zm.group(1)

    clean_addr = address_str.strip()
    clean_addr = re.sub(r',?\s*Việt Nam$', '', clean_addr, flags=re.IGNORECASE)
    
    parts = [p.strip() for p in clean_addr.split(',')]
    
    for pref in PROVINCES:
        for part in reversed(parts):
            if pref.lower() in part.lower():
                state_name = pref
                break
        if state_name:
            break
            
    if not state_name and default_loc:
        for pref in PROVINCES:
            if pref.lower() in default_loc.lower():
                state_name = pref
                break
                
    if len(parts) >= 2:
        district_part = parts[-2]
        if "Quận" in district_part or "Huyện" in district_part or "Thị xã" in district_part or "Thành phố" in district_part:
            city_only = district_part.replace("Quận", "").replace("Huyện", "").replace("Thị xã", "").replace("Thành phố", "").strip()
        else:
            city_only = district_part
    elif default_loc:
        city_only = default_loc
    else:
        city_only = state_name

    return zip_code, state_name, city_only

def generate_vn_slug(name, city_only, address):
    raw = f"{name} {city_only}".strip()
    clean = remove_vietnamese_accents(raw)
    clean = re.sub(r'[\s/\\?%*:|"<>#&,.]+', '-', clean).strip('-').lower()
    if not clean:
        clean = "cong-ty"
    h = hashlib.md5(f"{name}_{address}".encode('utf-8')).hexdigest()[:6]
    return f"{clean[:150]}-{h}"

def save_batch_to_database(data_tuples):
    if not data_tuples:
        return 0
    try:
        payload_data = []
        for t in data_tuples:
            payload_data.append({
                "name": t[0],
                "address": t[1],
                "phone": t[2],
                "website": t[3],
                "email": t[4],
                "image_url": t[5],
                "url": t[6],
                "search_city": t[7],
                "city_only": t[8],
                "state_name": t[9],
                "state": t[10],
                "zip_code": t[11],
                "latitude": t[12],
                "longitude": t[13],
                "rating": t[14],
                "reviews_count": t[15],
                "category": t[16],
                "slug": t[17],
                "working_hours": t[18]
            })
            
        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": API_KEY
        }
        response = requests.post(API_URL, json={"data": payload_data}, headers=headers, timeout=30)
        
        if response.status_code == 200:
            inserted_count = len(payload_data)
            print(f"  [+] Đã gửi lô {inserted_count} bản ghi lên API.")
            return inserted_count
        else:
            print(f"  [!] Lỗi gửi API: HTTP {response.status_code} - {response.text}")
            return 0
    except Exception as e:
        print(f"  [!] Lỗi kết nối API: {e}")
        return 0

def save_to_database(data, default_loc, category):
    if not data:
        return 0
    batch = []
    for r in data:
        name = r.get('name', '').strip()
        address = r.get('address', '').strip()
        if not name:
            continue
            
        zip_code, state_name, city_only = parse_vn_address(address, default_loc)
        slug = generate_vn_slug(name, city_only, address)
        search_city = f"{state_name} {city_only}".strip() if state_name and city_only else (default_loc or city_only)

        batch.append((
            name,
            address,
            r.get('phone', ''),
            r.get('website', ''),
            r.get('email', ''),
            r.get('image_url', '[]'),
            r.get('url', ''),
            search_city,
            city_only,
            state_name,
            state_name,
            zip_code,
            r.get('latitude', None),
            r.get('longitude', None),
            r.get('rating', 0.0),
            r.get('reviews_count', 0),
            category,
            slug,
            r.get('working_hours', '[]')
        ))
    return save_batch_to_database(batch)

async def extract_email_from_website(page, website_url):
    email = ""
    try:
        await page.goto(website_url, wait_until="domcontentloaded", timeout=12000)
        content = await page.content()
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', content)
        valid_emails = [e for e in emails if not e.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg')) and "wixpress" not in e and "sentry" not in e and "example" not in e]
        if valid_emails:
            email = valid_emails[0]
    except:
        pass
    return email

async def scrape_google_maps_with_page(page, search_query: str):
    encoded_query = search_query.replace(" ", "+")
    url = f"https://www.google.com/maps/search/{encoded_query}?hl=vi"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        return []

    feed_selector = 'div[role="feed"]'
    try:
        await page.wait_for_selector(feed_selector, timeout=10000)
    except Exception:
        # Kiểm tra nếu ra thẳng 1 địa điểm duy nhất (single place result)
        title_single = await page.query_selector('h1')
        if title_single:
            place_url = page.url
            if "/maps/place/" in place_url:
                single_res = await parse_single_place(page, place_url)
                return [single_res] if single_res else []
        return []

    feed_element = await page.query_selector(feed_selector)
    previous_count = 0
    scroll_attempts = 0
    while scroll_attempts < 25:
        await page.evaluate('(feed) => feed.scrollTop = feed.scrollHeight', feed_element)
        await asyncio.sleep(1.8)
        items = await page.query_selector_all('div[role="feed"] > div > div[role="article"]')
        current_count = len(items)
        end_indicator = await page.query_selector("text=Bạn đã xem hết danh sách, text=Không tìm thấy kết quả nào, text=You've reached the end of the list")
        if end_indicator or (current_count == previous_count and current_count > 0):
            break
        previous_count = current_count
        scroll_attempts += 1

    articles = await page.query_selector_all('div[role="feed"] > div > div[role="article"]')
    place_urls = []
    for article in articles:
        try:
            link_elem = await article.query_selector('a[href*="/maps/place/"]')
            if link_elem:
                href = await link_elem.get_attribute('href')
                if href and href not in place_urls:
                    place_urls.append(href)
        except:
            continue

    results = []
    for place_url in place_urls:
        try:
            res = await parse_single_place(page, place_url)
            if res and res.get('name'):
                results.append(res)
        except Exception:
            continue

    return results

async def parse_single_place(page, place_url):
    try:
        await page.goto(place_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_selector('h1', timeout=10000)
        
        title_elem = await page.query_selector('h1')
        name = (await title_elem.inner_text()).strip() if title_elem else ""
        if not name:
            return None

        latitude, longitude = None, None
        coords_match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', page.url)
        if coords_match:
            latitude = float(coords_match.group(1))
            longitude = float(coords_match.group(2))

        # --- Địa chỉ (Address) ---
        address = ""
        addr_btn = await page.query_selector('button[data-item-id="address"]')
        if addr_btn:
            address = await addr_btn.get_attribute('aria-label')
            if address:
                address = re.sub(r'^(?:Địa chỉ:\s*|Address:\s*)', '', address).strip()

        # --- Số điện thoại (Phone) ---
        phone = ""
        phone_btn = await page.query_selector('button[data-item-id^="phone:tel:"]')
        if phone_btn:
            phone = await phone_btn.get_attribute('aria-label')
            if phone:
                phone = re.sub(r'^(?:Số điện thoại:\s*|Phone:\s*)', '', phone).strip()

        # --- 公式サイト (Website) ---
        website = ""
        web_btn = await page.query_selector('a[data-item-id="authority"]')
        if web_btn:
            website = await web_btn.get_attribute('href')

        # --- 画像 (Image URLs) ---
        image_url = "[]"
        try:
            images = await page.evaluate('''() => {
                return Array.from(document.querySelectorAll('img'))
                    .map(img => img.src)
                    .filter(src => src.includes('lh3.googleusercontent.com') && !src.includes('=w32'));
            }''')
            if images:
                image_urls = []
                seen = set()
                for raw_img in images:
                    if len(image_urls) >= 10: break
                    if raw_img not in seen:
                        seen.add(raw_img)
                        image_urls.append(raw_img)
                image_url = json.dumps(image_urls, ensure_ascii=False)
        except:
            pass

        email = ""
        if website:
            email = await extract_email_from_website(page, website)

        # --- Giờ hoạt động (Working Hours) ---
        working_hours = "[]"
        try:
            expand_btn = await page.query_selector('[aria-label*="Giờ hoạt động" i], [aria-label*="Giờ mở cửa" i], [aria-label*="open hours" i], [data-item-id="oh"]')
            if expand_btn:
                await expand_btn.click()
                await asyncio.sleep(0.8)
            
            hours_json = await page.evaluate('''() => {
                let table = document.querySelector('table');
                let rows = [];
                if (table) {
                    let trs = table.querySelectorAll('tr');
                    trs.forEach(tr => {
                        let day = tr.querySelector('td:nth-child(1)');
                        let time = tr.querySelector('td:nth-child(2)');
                        if (day && time) {
                            rows.push(day.innerText.trim() + ": " + time.innerText.trim());
                        }
                    });
                }
                if (rows.length >= 7) return JSON.stringify(rows);
                
                let labels = document.querySelectorAll('[aria-label]');
                for (let el of labels) {
                    let text = el.getAttribute('aria-label') || '';
                    if (text.includes("Thứ Hai") && text.includes("Thứ Ba")) {
                        return JSON.stringify([text]);
                    }
                }
                return "[]";
            }''')
            if hours_json and hours_json != "[]":
                working_hours = hours_json
        except:
            pass

        # --- Đánh giá (Rating & Reviews) ---
        rating_data = await page.evaluate('''() => {
            let rating = 0.0;
            let reviews = 0;
            try {
                let starSpan = document.querySelector('span[aria-label*="sao"], span[aria-label*="star"]');
                if (starSpan) {
                    let text = starSpan.getAttribute('aria-label') || '';
                    let mRating = text.match(/([0-9.]+)\\s*(?:sao|stars?)/i) || text.match(/(?:sao|stars?)\\s*([0-9.]+)/i);
                    let mReviews = text.match(/([0-9,]+)\\s*(?:bài đánh giá|Reviews?)/i);
                    if (mRating) rating = parseFloat(mRating[1]);
                    if (mReviews) reviews = parseInt(mReviews[1].replace(/,/g, ''));
                }
                if (rating === 0.0) {
                    let reviewText = document.querySelector('.F7nice');
                    if (reviewText) {
                        let text2 = reviewText.innerText;
                        let m2 = text2.match(/([0-9.]+)[^0-9]*\\(([0-9,]+)\\)/);
                        if (m2) {
                            rating = parseFloat(m2[1]);
                            reviews = parseInt(m2[2].replace(/,/g, ''));
                        }
                    }
                }
            } catch (e) {}
            return { rating, reviews };
        }''')

        return {
            "name": name,
            "address": address,
            "phone": phone,
            "website": website,
            "email": email,
            "image_url": image_url,
            "url": place_url,
            "latitude": latitude,
            "longitude": longitude,
            "rating": rating_data.get("rating", 0.0),
            "reviews_count": rating_data.get("reviews", 0),
            "working_hours": working_hours
        }
    except Exception as e:
        return None

def get_vn_locations(txt_path="vn_locations.txt"):
    if not os.path.exists(txt_path):
        txt_path = os.path.join(os.path.dirname(__file__), "vn_locations.txt")
    if not os.path.exists(txt_path): return PROVINCES
    with open(txt_path, mode='r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def get_categories(txt_path="vn_categories.txt"):
    if not os.path.exists(txt_path):
        txt_path = os.path.join(os.path.dirname(__file__), "vn_categories.txt")
    if not os.path.exists(txt_path):
        txt_path = os.path.join(os.path.dirname(__file__), "categories.txt")
    if not os.path.exists(txt_path): return ["Công ty", "Nhà hàng", "Luật sư"]
    with open(txt_path, mode='r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def claim_task(query_str, loc, category):
    try:
        headers = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}
        payload = {"query_str": query_str, "loc": loc, "category": category}
        response = requests.post(API_TASK_CLAIM_URL, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            data = response.json()
            return data.get('claimed', False)
        elif response.status_code in (401, 403):
            print(f"[!] Lỗi xác thực API (HTTP {response.status_code}). Vui lòng kiểm tra lại CRAWLER_API_KEY. Dừng tool!")
            os._exit(1) # Dừng hoàn toàn tiến trình nếu sai key
        else:
            print(f"  [!] Lỗi claim_task API: HTTP {response.status_code} - {response.text}")
    except Exception as e:
        print(f"[!] Lỗi kết nối API xin việc: {e}")
    return False

def complete_task(query_str):
    try:
        headers = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}
        response = requests.post(API_TASK_COMPLETE_URL, json={"query_str": query_str}, headers=headers, timeout=30)
        if response.status_code not in (200, 201):
            print(f"  [!] Lỗi complete_task API: HTTP {response.status_code} - {response.text}")
        else:
            print(f"  [v] Đã chốt xong (Hoàn thành): {query_str}")
    except Exception as e:
        print(f"[!] Lỗi kết nối API báo cáo hoàn thành: {e}")

def fail_task(query_str):
    try:
        # Tự động thay đổi url claim thành fail
        API_TASK_FAIL_URL = API_TASK_CLAIM_URL.replace("/claim", "/fail") if API_TASK_CLAIM_URL else "https://jplocalhub.com/api/crawler/task/fail"
        headers = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}
        response = requests.post(API_TASK_FAIL_URL, json={"query_str": query_str}, headers=headers, timeout=30)
        if response.status_code not in (200, 201):
            print(f"  [!] Lỗi fail_task API: HTTP {response.status_code} - {response.text}")
        else:
            print(f"  [x] Đã đánh dấu LỖI: {query_str}")
    except Exception as e:
        print(f"[!] Lỗi kết nối API báo lỗi: {e}")

GLOBAL_BATCH = []

async def process_query(sem, browser, query_str, default_loc, category, write_lock):
    global GLOBAL_BATCH
    async with sem:
        print(f"[*] Bắt đầu cào: {query_str}")
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            geolocation={"latitude": 35.6762, "longitude": 139.6503},
            permissions=["geolocation"]
        )
        page = await context.new_page()
        
        try:
            data = await scrape_google_maps_with_page(page, query_str)
            found_count = len(data) if data else 0
            print(f"  -> {query_str}: Cào được {found_count} kết quả.")
            
            if data:
                async with write_lock:
                    for r in data:
                        name = r.get('name', '').strip()
                        address = r.get('address', '').strip()
                        if not name:
                            continue
                        zip_code, state_name, city_only = parse_vn_address(address, default_loc)
                        slug = generate_vn_slug(name, city_only, address)
                        search_city = f"{state_name} {city_only}".strip() if state_name and city_only else (default_loc or city_only)

                        GLOBAL_BATCH.append((
                            name,
                            address,
                            r.get('phone', ''),
                            r.get('website', ''),
                            r.get('email', ''),
                            r.get('image_url', '[]'),
                            r.get('url', ''),
                            search_city,
                            city_only,
                            state_name,
                            state_name,
                            zip_code,
                            r.get('latitude', None),
                            r.get('longitude', None),
                            r.get('rating', 0.0),
                            r.get('reviews_count', 0),
                            category,
                            slug,
                            r.get('working_hours', '[]')
                        ))
                    
                    if len(GLOBAL_BATCH) >= 50:
                        batch_to_save = GLOBAL_BATCH[:]
                        GLOBAL_BATCH.clear()
                        await asyncio.to_thread(save_batch_to_database, batch_to_save)
            
            # Ghi nhận hoàn thành lên API
            await asyncio.to_thread(complete_task, query_str)
        except Exception as e:
            print(f"[!] Lỗi ở {query_str}: {e}")
            # Ghi nhận thất bại lên API
            await asyncio.to_thread(fail_task, query_str)
        finally:
            await context.close()
            
        delay = random.uniform(2.0, 4.5)
        await asyncio.sleep(delay)

async def main():
    print("Khởi tạo tiến trình Crawler qua API (Local + DB Sync)...")
    
    locations = get_vn_locations()
    categories = get_categories()
    
    print(f"====================================")
    print(f"TỔNG SỐ ĐỊA ĐIỂM VIỆT NAM: {len(locations)}")
    print(f"TỔNG SỐ NGÀNH NGHỀ: {len(categories)}")
    print(f"====================================")
    
    write_lock = asyncio.Lock()
    sem = asyncio.Semaphore(3) # Can increase concurrency since it's local iteration now
    
    start_time = time.time()
    max_duration = 4 * 3600 + 45 * 60 # 4 hours 45 minutes
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        all_tasks_data = []
        for category in categories:
            for loc in locations:
                query_str = f"{category} ở {loc}"
                all_tasks_data.append((query_str, loc, category))
                    
        random.shuffle(all_tasks_data)
        print(f"Đã chuẩn bị {len(all_tasks_data)} từ khóa ngẫu nhiên!")
        print("\nBắt đầu chạy xin việc qua API!")
        
        tasks = []
        
        for query_str, loc, category in all_tasks_data:
            elapsed = time.time() - start_time
            if elapsed > max_duration:
                print("\n[!] Đã hết thời gian phiên làm việc (4h45p). Thoát an toàn để chờ GitHub Actions restart.")
                break
                
            claimed = await asyncio.to_thread(claim_task, query_str, loc, category)
            if not claimed:
                continue # Already processed or processing by someone else
                
            task = asyncio.create_task(process_query(sem, browser, query_str, loc, category, write_lock))
            tasks.append(task)
            
            # Ghi nhận hoàn thành lên API inside process_query ideally, but we can do it after gather
            if len(tasks) >= 15:
                await asyncio.gather(*tasks)
                tasks = []
                
        if tasks:
            await asyncio.gather(*tasks)

        # Kiểm tra xem có dư data nào chưa push không
        async with write_lock:
            if GLOBAL_BATCH:
                batch_to_save = GLOBAL_BATCH[:]
                GLOBAL_BATCH.clear()
                await asyncio.to_thread(save_batch_to_database, batch_to_save)
            
        await browser.close()

        
    print("\nHoàn thành toàn bộ!")

if __name__ == "__main__":
    asyncio.run(main())
