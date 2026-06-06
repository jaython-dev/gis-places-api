"""
Google Maps POI Scraper — Grid-based, ~10m resolution
Extracts: name, category, address, lat, lon, rating, reviews, phone, website
Output: CSV + GeoJSON

Requirements:
    pip install playwright pandas
    playwright install chromium

Usage:
    python gmaps_poi_scraper.py
"""

import asyncio
import json
import csv
import time
import re
import argparse
import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from playwright.async_api import async_playwright, Page
from shapely.geometry import shape, Point as ShapelyPoint, Polygon
from shapely.ops import unary_union

# ─────────────────────────────────────────────
#  CONFIGURATION — edit these before running
# ─────────────────────────────────────────────

# # Bounding box for Vadodara (min_lat, min_lon, max_lat, max_lon)
# BBOX = (22.255, 73.115, 22.395, 73.255)

# Bounding box for Alkapuri (min_lat, min_lon, max_lat, max_lon)
BBOX = (
    22.30602315776565,  # minLat
    73.1616261257188,   # minLon
    22.32123897140427,  # maxLat
    73.18153294773111   # maxLon
)

# Grid step in degrees
# Use 0.002 (~220m) for denser coverage, 0.01 (~1.1km) for faster run
GRID_STEP = 0.01   # ~1.1km grid — fast run that will finish in a few hours

# POI categories to search (Google Maps search terms)
SEARCH_CATEGORIES = [
    "restaurant",
    "cafe",
    "hospital",
    "clinic",
    "pharmacy",
    "shop",
    "snacks",
    "farsan",
    "hotel",
    "school",
    "bank",
    "petrol pump",
    "supermarket",
    "bakery",
    "sweet shop",
    "juice shop",
    "grocery",
    "temple",
    "mosque",
    "church",
    "gym",
    "salon",
    "hardware store",
    "clothing store",
    "medical store",
]

HEADLESS       = True    # Set False to watch the browser (useful for debugging)
SCROLL_TIMES   = 8       # How many times to scroll results panel per search
DELAY_BETWEEN  = 1.2     # Seconds between requests (be polite)

# ─────────────────────────────────────────────

@dataclass
class POI:
    name: str = ""
    category: str = ""
    address: str = ""
    lat: float = 0.0
    lon: float = 0.0
    rating: str = ""
    reviews: str = ""
    phone: str = ""
    website: str = ""
    place_id: str = ""
    search_term: str = ""
    grid_lat: float = 0.0
    grid_lon: float = 0.0


def sanitize_filename(name):
    """Sanitize the ward name to construct a clean filename suffix."""
    match = re.search(r'WARD NO:\s*(\d+)', name, re.IGNORECASE)
    if match:
        return f"ward_{match.group(1)}"
    
    # Otherwise replace special chars with underscores
    name_clean = name.lower()
    name_clean = re.sub(r'[^a-z0-9]+', '_', name_clean)
    return name_clean.strip('_')


def parse_kml_boundary(kml_path, ward_query=None, list_wards=False):
    """
    Parses a KML file. If ward_query is provided, filters by the Vill_name containing ward_query.
    Otherwise, prompts user interactively or combines all boundaries.
    """
    if not os.path.exists(kml_path):
        raise FileNotFoundError(f"KML boundary file not found: {kml_path}")

    try:
        tree = ET.parse(kml_path)
        root = tree.getroot()
    except Exception as e:
        raise ValueError(f"Failed to parse KML file {kml_path}: {e}")

    # Namespaces are commonly used in KML files
    namespaces = {'kml': 'http://www.opengis.net/kml/2.2'}
    
    # Find all Placemark nodes
    placemarks = root.findall('.//kml:Placemark', namespaces)
    if not placemarks:
        placemarks = root.findall('.//Placemark')

    if not placemarks:
        raise ValueError(f"No Placemarks found in KML file: {kml_path}")

    # Extract all wards with their names and geometries
    wards_data = []
    for idx, pm in enumerate(placemarks):
        # Extract name from Vill_name SimpleData or PM name tag
        name = None
        simple_datas = pm.findall('.//kml:SimpleData', namespaces) or pm.findall('.//SimpleData')
        for sd in simple_datas:
            if sd.attrib.get('name') == 'Vill_name':
                name = sd.text
                break
        
        if not name:
            name_node = pm.find('kml:name', namespaces)
            if name_node is None:
                name_node = pm.find('name')
            if name_node is not None:
                name = name_node.text

        if not name:
            name = f"Placemark {idx + 1}"

        name = name.strip()

        # Parse geometry (supporting MultiGeometry/Polygon structure)
        polys = pm.findall('.//kml:Polygon', namespaces) or pm.findall('.//Polygon')
        pm_geoms = []
        for poly in polys:
            coord_node = poly.find('.//kml:coordinates', namespaces)
            if coord_node is None:
                coord_node = poly.find('.//coordinates')
            if coord_node is not None and coord_node.text:
                coords_str = coord_node.text.strip()
                coords = []
                for pt_str in coords_str.split():
                    parts = pt_str.split(',')
                    if len(parts) >= 2:
                        try:
                            lon = float(parts[0])
                            lat = float(parts[1])
                            coords.append((lon, lat))
                        except ValueError:
                            pass
                if len(coords) >= 3:
                    pm_geoms.append(Polygon(coords))
        
        if pm_geoms:
            pm_shape = unary_union(pm_geoms)
            wards_data.append({
                "index": idx + 1,
                "name": name,
                "shape": pm_shape
            })

    if list_wards:
        print("\nAvailable wards in KML:")
        print(f"0: All Wards (Stitched)")
        for wd in wards_data:
            print(f"{wd['index']}: {wd['name']}")
        print()
        return None, None

    # Determine chosen ward
    chosen_ward = None
    if ward_query is None:
        print("\nAvailable wards in KML:")
        print(f"0: All Wards (Stitched)")
        for wd in wards_data:
            print(f"{wd['index']}: {wd['name']}")
        print()
        
        while True:
            try:
                choice = input(f"Select a ward to scrape (enter number 0-{len(wards_data)}): ").strip()
                choice_idx = int(choice)
                if 0 <= choice_idx <= len(wards_data):
                    if choice_idx == 0:
                        chosen_ward = "all"
                    else:
                        chosen_ward = wards_data[choice_idx - 1]
                    break
                else:
                    print(f"Please enter a number between 0 and {len(wards_data)}.")
            except ValueError:
                print("Invalid input. Please enter a valid number.")
            except (KeyboardInterrupt, EOFError):
                print("\nScraping cancelled.")
                exit(0)
    else:
        # Match by ward_query (can be index number or partial string match)
        query_clean = ward_query.strip().lower()
        chosen_ward = None
        
        if query_clean in ("0", "all"):
            chosen_ward = "all"
        else:
            matches = []
            
            # 1. If it's a digit, check for ward number match in the name (e.g. "WARD NO: 19")
            if query_clean.isdigit():
                query_num = int(query_clean)
                import re
                for wd in wards_data:
                    num_match = re.search(r'WARD\s+NO:\s*(\d+)', wd['name'], re.IGNORECASE)
                    if num_match:
                        wd_num = int(num_match.group(1))
                        if wd_num == query_num:
                            matches.append(wd)
            
            # 2. Case-insensitive exact name match
            if not matches:
                for wd in wards_data:
                    if query_clean == wd['name'].lower():
                        matches.append(wd)
            
            # 3. Case-insensitive substring match
            if not matches:
                for wd in wards_data:
                    if query_clean in wd['name'].lower():
                        matches.append(wd)
            
            # 4. Fallback to list index matching (if query is a number and within 1..len(wards_data))
            if not matches and query_clean.isdigit():
                query_idx = int(query_clean)
                if 1 <= query_idx <= len(wards_data):
                    chosen_ward = wards_data[query_idx - 1]
                    print(f"Warning: Match by ward number/name failed. Falling back to list index selection.")
            
            # Handle matches
            if chosen_ward is None:
                if not matches:
                    raise ValueError(f"No ward matching '{ward_query}' found in KML.")
                elif len(matches) == 1:
                    chosen_ward = matches[0]
                else:
                    print(f"\nMultiple wards matched query '{ward_query}':")
                    for i, m in enumerate(matches):
                        print(f"{i + 1}: {m['name']}")
                    while True:
                        try:
                            choice = input(f"Select a ward (1-{len(matches)}): ").strip()
                            choice_idx = int(choice)
                            if 1 <= choice_idx <= len(matches):
                                chosen_ward = matches[choice_idx - 1]
                                break
                        except ValueError:
                            print("Invalid input.")
                        except (KeyboardInterrupt, EOFError):
                            print("\nScraping cancelled.")
                            exit(0)

    # Combine shapes
    if chosen_ward == "all":
        all_shapes = [wd['shape'] for wd in wards_data]
        combined_shape = unary_union(all_shapes)
        ward_label = "all_wards"
        print(f"Stitched all {len(wards_data)} wards together.")
    else:
        combined_shape = chosen_ward['shape']
        ward_label = chosen_ward['name']
        print(f"Selected ward: {chosen_ward['name']}")

    return combined_shape, ward_label


def generate_grid(bbox, step, boundary_shape=None):
    """Generate lat/lon grid points covering the bounding box, optionally filtered by boundary_shape."""
    min_lat, min_lon, max_lat, max_lon = bbox
    points = []
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            pt = (round(lat, 6), round(lon, 6))
            if boundary_shape:
                sh_point = ShapelyPoint(lon, lat)
                if boundary_shape.contains(sh_point):
                    points.append(pt)
            else:
                points.append(pt)
            lon += step
        lat += step
    return points


def extract_coords_from_url(url: str):
    """Parse lat/lon from a Google Maps URL."""
    # Pattern: @lat,lon,zoom or !3dlat!4dlon
    m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def extract_place_id(url: str):
    m = re.search(r'place/([^/]+)/', url)
    return m.group(1) if m else ""


async def scrape_place_details(page: Page, url: str, category: str, grid_lat: float, grid_lon: float) -> Optional[POI]:
    """Open a single place page and extract all details."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        # Wait up to 5 seconds for the URL to update and contain coordinate details
        try:
            for _ in range(25):
                if re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', page.url) or re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', page.url):
                    break
                await asyncio.sleep(0.2)
        except:
            pass

        # Wait up to 3 seconds for the address/details element to be attached (indicates details panel is loaded)
        try:
            await page.locator('[data-item-id="address"]').first.wait_for(state="attached", timeout=3000)
        except:
            pass

        poi = POI(search_term=category, grid_lat=grid_lat, grid_lon=grid_lon)

        # Name
        try:
            poi.name = await page.locator('h1.DUwDvf').first.inner_text(timeout=3000)
        except:
            try:
                poi.name = await page.locator('[data-attrid="title"] span').first.inner_text(timeout=2000)
            except:
                pass

        # Category
        try:
            poi.category = await page.locator('button.DkEaL').first.inner_text(timeout=2000)
        except:
            poi.category = category

        # Address
        try:
            addr_el = page.locator('[data-item-id="address"]')
            if await addr_el.count() > 0:
                poi.address = await addr_el.first.get_attribute('aria-label') or ""
                poi.address = poi.address.replace("Address: ", "")
        except:
            pass

        # Rating
        try:
            poi.rating = await page.locator('div.F7nice span[aria-hidden="true"]').first.inner_text(timeout=2000)
        except:
            pass

        # Review count
        try:
            container_text = await page.locator('div.F7nice').first.inner_text(timeout=2000)
            match = re.search(r'\(([\d,.]+K?)\)', container_text)
            if match:
                poi.reviews = match.group(1)
            else:
                text = await page.locator('div.F7nice span[aria-label]').first.get_attribute('aria-label')
                if text:
                    poi.reviews = re.sub(r'[^\d,]', '', text)
        except:
            pass

        # Phone
        try:
            phone_el = page.locator('[data-item-id^="phone:tel"]')
            if await phone_el.count() > 0:
                poi.phone = await phone_el.first.get_attribute('aria-label') or ""
                poi.phone = poi.phone.replace("Phone: ", "")
        except:
            pass

        # Website
        try:
            web_el = page.locator('[data-item-id="authority"]')
            if await web_el.count() > 0:
                poi.website = await web_el.first.get_attribute('href') or ""
        except:
            pass

        # Coordinates and Place ID from URL or page source
        current_url = page.url
        poi.lat, poi.lon = extract_coords_from_url(current_url)
        try:
            content = await page.content()
            m = re.search(r'(ChIJ[0-9A-Za-z_-]{23})', content)
            if m:
                poi.place_id = m.group(1)
            else:
                poi.place_id = extract_place_id(current_url)
        except:
            poi.place_id = extract_place_id(current_url)

        return poi if poi.name else None

    except Exception as e:
        return None


async def search_at_point(page: Page, lat: float, lon: float, category: str, zoom: int = 17) -> List[str]:
    """
    Navigate Google Maps to a specific lat/lon at a given zoom level
    and search for a category. Returns list of place URLs found.
    """
    search_query = f"{category} near {lat},{lon}"
    encoded = search_query.replace(' ', '+')
    url = f"https://www.google.com/maps/search/{encoded}/@{lat},{lon},{zoom}z"

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)

        # Scroll results panel to load more
        place_urls = set()
        results_panel = page.locator('div[role="feed"]')

        for _ in range(SCROLL_TIMES):
            # Collect all place links visible
            links = await page.locator('a[href*="/maps/place/"]').all()
            for link in links:
                href = await link.get_attribute('href')
                if href and '/maps/place/' in href:
                    # Clean to base place URL
                    href = href.split('?')[0]
                    place_urls.add(href)

            # Scroll down in the results panel
            try:
                await results_panel.evaluate('el => el.scrollBy(0, 400)')
            except:
                await page.keyboard.press('PageDown')
            await page.wait_for_timeout(600)

        return list(place_urls)

    except Exception as e:
        print(f"  ⚠ Search error at ({lat},{lon}) for '{category}': {e}")
        return []


def save_csv(pois: List[POI], filepath: str):
    if not pois:
        return
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(pois[0]).keys()))
        writer.writeheader()
        writer.writerows([asdict(p) for p in pois])
    print(f"✅ CSV saved: {filepath} ({len(pois)} records)")


def save_geojson(pois: List[POI], filepath: str):
    features = []
    for p in pois:
        if p.lat and p.lon:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p.lon, p.lat]},
                "properties": {
                    "name": p.name,
                    "category": p.category,
                    "address": p.address,
                    "rating": p.rating,
                    "reviews": p.reviews,
                    "phone": p.phone,
                    "website": p.website,
                    "place_id": p.place_id,
                    "search_term": p.search_term,
                }
            })
    geojson = {"type": "FeatureCollection", "features": features}
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
    print(f"✅ GeoJSON saved: {filepath} ({len(features)} features)")


async def worker(
    worker_id: int,
    queue: asyncio.Queue,
    browser,
    total_tasks: int,
    all_pois: List[POI],
    seen_place_ids: set,
    seen_names_coords: set,
    lock: asyncio.Lock,
    output_csv: str,
    output_geojson: str,
    bbox_buffer: float = 0.0,
    zoom: int = 17,
    boundary_shape=None
):
    """Worker coroutine that processes grid/category searches in parallel."""
    # Create isolated context for each worker to maintain clean sessions
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    )
    page = await context.new_page()

    # Accept cookies / language prompt if shown
    await page.goto("https://www.google.com/maps", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    try:
        await page.click('button[aria-label="Accept all"]', timeout=3000)
    except:
        pass

    while True:
        try:
            task = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        actual_idx, lat, lon, cat = task
        print(f"[Worker {worker_id}][{actual_idx+1}/{total_tasks}] Searching '{cat}' @ ({lat}, {lon})")

        place_urls = await search_at_point(page, lat, lon, cat, zoom=zoom)
        print(f"[Worker {worker_id}] Found {len(place_urls)} place links")

        for url in place_urls:
            poi = await scrape_place_details(page, url, cat, lat, lon)
            if poi:
                if poi.lat is None or poi.lon is None:
                    print(f"[Worker {worker_id}]   ✗ Skipped (No coordinates found): {poi.name}")
                    continue

                # Filter by BBOX or Boundary to avoid out-of-location results
                if boundary_shape:
                    sh_point = ShapelyPoint(poi.lon, poi.lat)
                    if not boundary_shape.contains(sh_point):
                        print(f"[Worker {worker_id}]   ✗ Skipped (Out of Boundary): {poi.name} | {poi.lat},{poi.lon}")
                        continue
                else:
                    min_lat, min_lon, max_lat, max_lon = BBOX
                    if not ((min_lat - bbox_buffer) <= poi.lat <= (max_lat + bbox_buffer) and (min_lon - bbox_buffer) <= poi.lon <= (max_lon + bbox_buffer)):
                        print(f"[Worker {worker_id}]   ✗ Skipped (Out of BBox): {poi.name} | {poi.lat},{poi.lon}")
                        continue

                # Deduplicate and append to results list
                if poi.place_id and poi.place_id in seen_place_ids:
                    continue
                coord_key = (poi.name.lower(), round(poi.lat, 4), round(poi.lon, 4))
                if coord_key in seen_names_coords:
                    continue

                if poi.place_id:
                    seen_place_ids.add(poi.place_id)
                seen_names_coords.add(coord_key)

                # Acquire lock when modifying the shared list & writing to disk
                async with lock:
                    all_pois.append(poi)
                    print(f"[Worker {worker_id}]   ✓ {poi.name} | {poi.category} | {poi.lat},{poi.lon}")

                    # Save progress every 50 new POIs
                    if len(all_pois) % 50 == 0 and all_pois:
                        save_csv(all_pois, output_csv)
                        save_geojson(all_pois, output_geojson)

            await asyncio.sleep(DELAY_BETWEEN)
        
        queue.task_done()

    await context.close()


async def main(start_idx=0, end_idx=None, categories=None, output_prefix="vadodara", workers="auto", bbox_buffer=0.0, zoom=17, boundary_file=None, ward=None, list_wards=False):
    boundary_shape = None
    local_bbox = BBOX
    ward_suffix = ""
    
    if boundary_file and os.path.exists(boundary_file):
        if boundary_file.lower().endswith('.kml'):
            if list_wards:
                parse_kml_boundary(boundary_file, list_wards=True)
                return
            
            boundary_shape, ward_label = parse_kml_boundary(boundary_file, ward_query=ward)
            if ward_label:
                ward_suffix = f"_{sanitize_filename(ward_label)}"
        else:
            if list_wards:
                print("Error: --list-wards is only supported for KML boundary files.")
                return
            print(f"Loading boundary from: {boundary_file}")
            with open(boundary_file, 'r', encoding='utf-8') as f:
                geojson_data = json.load(f)
                
            if geojson_data.get('type') == 'FeatureCollection':
                geom_data = geojson_data['features'][0]['geometry']
            elif geojson_data.get('type') == 'Feature':
                geom_data = geojson_data['geometry']
            else:
                geom_data = geojson_data
                
            boundary_shape = shape(geom_data)
            
        if boundary_shape:
            minx, miny, maxx, maxy = boundary_shape.bounds
            local_bbox = (miny, minx, maxy, maxx)
        
    grid_points = generate_grid(local_bbox, GRID_STEP, boundary_shape)
    total_cells = len(grid_points)
    
    categories_to_search = categories if categories else SEARCH_CATEGORIES
    
    end_idx = end_idx if end_idx is not None else total_cells
    
    # Slice the grid points for this batch
    batch_points = grid_points[start_idx:end_idx]
    
    if categories:
        # Clean category names to be safe for filenames
        clean_cats = [re.sub(r'[^a-zA-Z0-9]', '_', c.lower().strip()) for c in categories_to_search]
        cat_str = "_".join(clean_cats)
    else:
        cat_str = "all"
    
    output_csv = f"{output_prefix}{ward_suffix}_{cat_str}_pois_{start_idx}_to_{end_idx}.csv"
    output_geojson = f"{output_prefix}{ward_suffix}_{cat_str}_pois_{start_idx}_to_{end_idx}.geojson"

    # Populate the task queue
    queue = asyncio.Queue()
    total_tasks = 0
    for i, (lat, lon) in enumerate(batch_points):
        actual_idx = start_idx + i
        for cat in categories_to_search:
            queue.put_nowait((actual_idx, lat, lon, cat))
            total_tasks += 1

    # Determine the number of workers
    if workers == "auto":
        # Safe default for memory and anti-bot limits: min of CPU count, 3, or total tasks
        num_workers = max(1, min(3, os.cpu_count() or 1, total_tasks))
    else:
        try:
            num_workers = max(1, int(workers))
        except ValueError:
            num_workers = 1

    print(f"📍 Grid: {total_cells} points total × {len(categories_to_search)} categories")
    print(f"   Running BATCH from index {start_idx} to {end_idx} ({len(batch_points)} points)")
    print(f"   Bounding box: {local_bbox}")
    print(f"   Grid step: {GRID_STEP}°")
    print(f"   Concurrency: {num_workers} worker(s) (requested: {workers})\n")

    all_pois: List[POI] = []
    seen_place_ids = set()
    seen_names_coords = set()
    lock = asyncio.Lock()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=[
                '--no-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--lang=en-US',
            ]
        )
        
        # Launch worker tasks
        tasks = []
        for w_id in range(num_workers):
            tasks.append(
                asyncio.create_task(
                    worker(
                        worker_id=w_id + 1,
                        queue=queue,
                        browser=browser,
                        total_tasks=total_tasks,
                        all_pois=all_pois,
                        seen_place_ids=seen_place_ids,
                        seen_names_coords=seen_names_coords,
                        lock=lock,
                        output_csv=output_csv,
                        output_geojson=output_geojson,
                        bbox_buffer=bbox_buffer,
                        zoom=zoom,
                        boundary_shape=boundary_shape
                    )
                )
            )
            
        # Wait for all workers to complete
        await asyncio.gather(*tasks)
        await browser.close()

    # Final save
    save_csv(all_pois, output_csv)
    save_geojson(all_pois, output_geojson)
    print(f"\n🎉 Done! Total unique POIs extracted in this batch: {len(all_pois)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google Maps POI Scraper Batch Runner")
    parser.add_argument("--start", type=int, default=0, help="Start index of grid points")
    parser.add_argument("--end", type=int, default=None, help="End index of grid points")
    parser.add_argument("--categories", type=str, default="", help="Comma-separated list of categories (e.g. 'cafe,hospital')")
    parser.add_argument("--output-prefix", type=str, default="vadodara", help="Prefix for the output CSV and GeoJSON files")
    parser.add_argument("--workers", type=str, default="auto", help="Number of concurrent workers or 'auto'")
    parser.add_argument("--bbox-buffer", type=float, default=0.0, help="Geographic bounding box search buffer in degrees (default: 0.0 for strict check)")
    parser.add_argument("--zoom", type=int, default=17, help="Google Maps zoom level (default: 17, use 19 or 20 for 10m scale)")
    parser.add_argument("--boundary", type=str, default=None, help="Path to a GeoJSON or KML boundary file to filter grid points and POIs")
    parser.add_argument("--ward", type=str, default=None, help="Ward name or number/index to filter KML boundary")
    parser.add_argument("--list-wards", action="store_true", help="List all available wards in KML boundary file and exit")
    args = parser.parse_args()
    
    cats = [c.strip() for c in args.categories.split(",")] if args.categories else None
    
    asyncio.run(main(
        start_idx=args.start, 
        end_idx=args.end, 
        categories=cats, 
        output_prefix=args.output_prefix, 
        workers=args.workers, 
        bbox_buffer=args.bbox_buffer, 
        zoom=args.zoom,
        boundary_file=args.boundary,
        ward=args.ward,
        list_wards=args.list_wards
    ))
