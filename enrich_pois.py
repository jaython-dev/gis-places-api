import os
import sys
import json
import math
import re
import argparse
import asyncio
from playwright.async_api import async_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

POI_INPUT_PATH = "D:\\projects\\gis-places-api\\POI.json"
POI_OUTPUT_PATH = "D:\\projects\\gis-places-api\\POI_enriched.json"
GIS_PLACES_DIR = "D:\\projects\\gis-places-api"

def haversine(lon1, lat1, lon2, lat2):
    # Convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
    # Haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    r = 6371000 # Radius of earth in meters
    return c * r

def extract_coords_from_url(url: str):
    m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None

def parse_time_str(t_str):
    t_str = t_str.strip().upper()
    if t_str == "CLOSED":
        return None
    m = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?', t_str)
    if not m:
        return None
    hr = int(m.group(1))
    mn = int(m.group(2)) if m.group(2) else 0
    ampm = m.group(3)
    if ampm == "PM" and hr < 12:
        hr += 12
    elif ampm == "AM" and hr == 12:
        hr = 0
    return f"{hr:02d}:{mn:02d}"

def parse_hours(hours_str):
    # Parse string like "10 AM to 4 PM" or "10 AM - 4 PM" or "Open 24 hours"
    hours_str = hours_str.upper().strip()
    if "24 HOURS" in hours_str:
        return "00:00", "24:00"
    
    hours_str = hours_str.replace("TO", "-").replace("–", "-").replace("—", "-")
    parts = hours_str.split("-")
    if len(parts) == 2:
        op = parse_time_str(parts[0])
        cl = parse_time_str(parts[1])
        if op and cl:
            return op, cl
    return "", ""

def load_local_references():
    geojson_files = [os.path.join(GIS_PLACES_DIR, f) for f in os.listdir(GIS_PLACES_DIR) if f.endswith(".geojson")]
    db_features = []
    for gfile in geojson_files:
        try:
            with open(gfile, "r", encoding="utf-8") as f:
                data = json.load(f)
                feat_list = data.get("features", [])
                db_features.extend(feat_list)
        except Exception as e:
            print(f"Error loading {gfile}: {e}")
    print(f"Loaded {len(db_features)} local reference features from {len(geojson_files)} GeoJSON files.")
    return db_features

def do_local_match(features, db_features):
    print("Running local matching phase...")
    matched_count = 0
    for feat in features:
        props = feat.get("properties", {})
        # Check if details are missing
        needs_details = not props.get("Phone / Co") or not props.get("Website") or not props.get("Opening Ti") or not props.get("Closing Ti") or len(props.get("Address", "")) < 20
        
        if not needs_details:
            continue
            
        coords = feat.get("geometry", {}).get("coordinates", [])
        if len(coords) < 2:
            continue
        lon1, lat1 = coords
        
        best_match = None
        min_dist = float('inf')
        for ref_feat in db_features:
            ref_coords = ref_feat.get("geometry", {}).get("coordinates", [])
            if len(ref_coords) < 2:
                continue
            lon2, lat2 = ref_coords
            
            dist = haversine(lon1, lat1, lon2, lat2)
            if dist < 15: # strict match within 15 meters
                if dist < min_dist:
                    min_dist = dist
                    best_match = ref_feat
                    
        if best_match and min_dist < 15:
            ref_props = best_match.get("properties", {})
            # Fill missing details
            if not props.get("Phone / Co") and ref_props.get("phone"):
                props["Phone / Co"] = ref_props["phone"].strip()
            if not props.get("Website") and ref_props.get("website"):
                props["Website"] = ref_props["website"].strip()
            if len(props.get("Address", "")) < 20 and ref_props.get("address"):
                props["Address"] = ref_props["address"].strip()
            if props.get("Rating") is None and ref_props.get("rating"):
                try:
                    props["Rating"] = float(ref_props["rating"])
                except:
                    pass
            if props.get("No. of Rev") is None and ref_props.get("reviews"):
                try:
                    props["No. of Rev"] = int(ref_props["reviews"].replace(",", ""))
                except:
                    pass
            matched_count += 1
            
    print(f"Successfully matched and filled details for {matched_count} POIs using local reference files.")

async def scrape_poi_details(page, target_name, target_lat, target_lon, delay):
    query = f"{target_name}"
    search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}/@{target_lat},{target_lon},17z"
    
    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
    except Exception as e:
        print(f"  [Warning] Page load timeout for '{target_name}': {e}")
        return None

    # Accept cookies
    try:
        await page.click('button[aria-label="Accept all"]', timeout=1500)
        await page.wait_for_timeout(1000)
    except:
        pass

    is_detail_page = False
    try:
        name_el = await page.locator('h1.DUwDvf').first.inner_text(timeout=2000)
        if name_el:
            is_detail_page = True
    except:
        pass

    if not is_detail_page:
        # It's a list page, find matching links
        links = await page.locator('a[href*="/maps/place/"]').all()
        results = []
        for link in links:
            href = await link.get_attribute('href') or ""
            name = await link.get_attribute('aria-label') or ""
            if not name:
                name = await link.inner_text()
            
            lat, lon = extract_coords_from_url(href)
            if lat and lon:
                dist = haversine(target_lon, target_lat, lon, lat)
                results.append({
                    "name": name.split("\n")[0],
                    "href": href,
                    "lat": lat,
                    "lon": lon,
                    "distance": dist
                })
        
        results.sort(key=lambda x: x["distance"])
        if results and results[0]["distance"] < 150:
            best = results[0]
            detail_url = best["href"]
            if detail_url.startswith("/"):
                detail_url = "https://www.google.com" + detail_url
            try:
                await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
                is_detail_page = True
            except Exception as e:
                print(f"  [Warning] Detail page load timeout: {e}")
                
    if not is_detail_page:
        return None

    # We are on details page, extract info
    info = {}
    
    # Address
    try:
        addr_el = page.locator('[data-item-id="address"]')
        if await addr_el.count() > 0:
            info["address"] = (await addr_el.first.get_attribute('aria-label') or "").replace("Address: ", "").strip()
    except:
        pass

    # Phone
    try:
        phone_el = page.locator('[data-item-id^="phone:tel"]')
        if await phone_el.count() > 0:
            info["phone"] = (await phone_el.first.get_attribute('aria-label') or "").replace("Phone: ", "").strip()
    except:
        pass

    # Website
    try:
        web_el = page.locator('[data-item-id="authority"]')
        if await web_el.count() > 0:
            info["website"] = (await web_el.first.get_attribute('href') or "").strip()
    except:
        pass

    # Rating & Reviews
    try:
        rating_val = await page.locator('div.F7nice span[aria-hidden="true"]').first.inner_text(timeout=1000)
        if rating_val:
            info["rating"] = float(rating_val)
    except:
        pass

    try:
        container_text = await page.locator('div.F7nice').first.inner_text(timeout=1000)
        match = re.search(r'\(([\d,.]+K?)\)', container_text)
        if match:
            rev_str = match.group(1).replace(",", "")
            if 'K' in rev_str:
                info["reviews"] = int(float(rev_str.replace('K', '')) * 1000)
            else:
                info["reviews"] = int(rev_str)
    except:
        pass

    # Hours extraction
    try:
        # Check if the "Show open hours" expander button exists and click it
        show_btn = page.locator('[aria-label*="Show open hours for the week"]')
        if await show_btn.count() > 0:
            try:
                await show_btn.first.click(timeout=2000)
                await page.wait_for_timeout(1500)
            except:
                pass
        else:
            # Check if any weekday row is already visible. If not, try clicking fallback selectors
            has_tr = False
            for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
                if await page.locator(f'tr:has-text("{d}")').count() > 0:
                    has_tr = True
                    break
            if not has_tr:
                oh_el = page.locator('[data-item-id="oh"]')
                if await oh_el.count() > 0:
                    try:
                        await oh_el.first.click(timeout=2000)
                        await page.wait_for_timeout(1500)
                    except:
                        pass

        # Parse hours from tr elements
        rows = await page.locator('tr').all()
        hours_map = {}
        for r in rows:
            text = await r.inner_text()
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            lines = [l.replace('\t', '') for l in lines]
            lines = [l for l in lines if l and not any(0xE000 <= ord(c) <= 0xF8FF for c in l)]
            if len(lines) >= 2:
                day = lines[0].strip().upper()
                hrs = lines[1].strip()
                if day in ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]:
                    hours_map[day] = hrs
        
        selected_hours = ""
        for day in ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]:
            if day in hours_map and hours_map[day] != "Closed":
                selected_hours = hours_map[day]
                break
        
        if selected_hours:
            op, cl = parse_hours(selected_hours)
            if op and cl:
                info["opening_time"] = op
                info["closing_time"] = cl
    except Exception as e:
        pass

    await asyncio.sleep(delay)
    return info

async def run_scraper(features, limit, batch_size, delay):
    print("Initializing Playwright scraper...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--lang=en-US',
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        scraped_count = 0
        success_count = 0
        
        for idx, feat in enumerate(features):
            if limit and success_count >= limit:
                print(f"Reached scraping limit of {limit}. Stopping.")
                break
                
            props = feat.get("properties", {})
            # Skip if already attempted by the scraper in a previous run
            if props.get("Scrape Attempted"):
                continue

            # Check if it needs details
            needs_details = not props.get("Phone / Co") or not props.get("Website") or not props.get("Opening Ti") or not props.get("Closing Ti") or len(props.get("Address", "")) < 20
            
            if not needs_details:
                continue
                
            name = props.get("Name")
            lat = props.get("Latitude")
            lon = props.get("Longitude")
            
            if not name or lat is None or lon is None:
                continue
                
            print(f"[{scraped_count + 1}] Scraping: {name} @ ({lat}, {lon})")
            info = await scrape_poi_details(page, name, lat, lon, delay)
            scraped_count += 1
            
            # Mark as attempted
            props["Scrape Attempted"] = True
            
            if info:
                success_count += 1
                # Fill values
                if info.get("address"):
                    props["Address"] = info["address"]
                if info.get("phone"):
                    props["Phone / Co"] = info["phone"]
                if info.get("website"):
                    props["Website"] = info["website"]
                if info.get("rating") is not None:
                    props["Rating"] = info["rating"]
                if info.get("reviews") is not None:
                    props["No. of Rev"] = info["reviews"]
                if info.get("opening_time"):
                    props["Opening Ti"] = info["opening_time"]
                if info.get("closing_time"):
                    props["Closing Ti"] = info["closing_time"]
                
                print(f"  [Success] Phone={info.get('phone', '')} | Website={info.get('website', '')} | Hours={info.get('opening_time', '')}-{info.get('closing_time', '')}")
            else:
                print("  [No details found]")
                
            if scraped_count % batch_size == 0 and scraped_count > 0:
                print(f"Incremental save at {scraped_count} scraped attempts...")
                save_data(features)
                
        await browser.close()
        return success_count

def save_data(features):
    out_data = {
        "type": "FeatureCollection",
        "features": features
    }
    with open(POI_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"Saved current progress to: {POI_OUTPUT_PATH}")

def main():
    parser = argparse.ArgumentParser(description="Enrich POI details using local references and Playwright Google Maps scraper.")
    parser.add_argument("--limit", type=int, default=None, help="Max number of POIs to scrape with Playwright in this run")
    parser.add_argument("--batch-size", type=int, default=10, help="Save progress every N successful scrapes")
    parser.add_argument("--no-scrape", action="store_true", help="Only do the local matching phase and exit")
    parser.add_argument("--delay", type=float, default=1.5, help="Polite delay between scraping requests")
    parser.add_argument("--resume", action="store_true", help="Resume from POI_enriched.json if it exists")
    args = parser.parse_args()

    # Determine input file
    input_file = POI_INPUT_PATH
    if args.resume and os.path.exists(POI_OUTPUT_PATH):
        input_file = POI_OUTPUT_PATH
        print(f"Resuming from existing enriched file: {POI_OUTPUT_PATH}")
    else:
        print(f"Starting fresh from original file: {POI_INPUT_PATH}")

    if not os.path.exists(input_file):
        print(f"Error: Input file {input_file} not found.")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        poi_data = json.load(f)

    features = poi_data.get("features", [])
    print(f"Loaded {len(features)} features.")

    # Phase 1: Local matches
    db_features = load_local_references()
    do_local_match(features, db_features)
    
    # Save after local matches
    save_data(features)

    # Phase 2: Scraper (unless disabled)
    if not args.no_scrape:
        print(f"Starting Phase 2: Playwright scraper (limit={args.limit}, batch_size={args.batch_size})...")
        successes = asyncio.run(run_scraper(features, args.limit, args.batch_size, args.delay))
        print(f"Phase 2 complete. Successfully enriched {successes} new POIs via Playwright.")
        save_data(features)
    else:
        print("Scraping phase skipped as requested.")

if __name__ == "__main__":
    main()
