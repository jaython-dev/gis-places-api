import re
import asyncio
import json
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

async def scrape_grid_point(page, lat, lng, category):
    zoom_level = "17z" 
    url = f"https://www.google.com/maps/search/{category}/@{lat},{lng},{zoom_level}"
    
    try:
        await page.goto(url, timeout=60000)
        
        # 1. Wait for the sidebar listing container to actually appear in the DOM
        sidebar_selector = 'div[role="feed"]'
        try:
            await page.wait_for_selector(sidebar_selector, timeout=5000)
        except Exception as e:
            # If no sidebar appears, there are no results at this micro-coordinate
            print(f"DEBUG: Could not find sidebar {sidebar_selector}. Saving screenshot to debug.png")
            await page.screenshot(path="debug.png")
            return []
            
        # 2. Mimic human scrolling to force Google to lazy-load the hidden listings
        for _ in range(3): # Scrolls 3 times per grid point to reveal hidden items
            await page.evaluate(
                f"document.querySelector('{sidebar_selector}').scrollBy(0, 800)"
            )
            await page.wait_for_timeout(1200) # Give the network a second to load data
            
        # 3. Now extract the fully loaded HTML content
        html_content = await page.content()
        soup = BeautifulSoup(html_content, 'html.parser')
        
        local_results = []
        listings = soup.find_all('a', href=re.compile(r'/maps/place/'))
        
        for listing in listings:
            href = listing.get('href', '')
            name = listing.get('aria-label', '')
            
            # Check for coordinates in the !3d...!4d... format
            coord_match = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', href)
            if not coord_match:
                # Fallback to the old @lat,lng format
                coord_match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', href)
                
            if coord_match and name:
                local_results.append({
                    "Name": name,
                    "Latitude": coord_match.group(1),
                    "Longitude": coord_match.group(2),
                    "Category": category
                })
        return local_results
    except Exception as e:
        print(f"Error at point ({lat:.4f}, {lng:.4f}): {e}")
        return []

async def main():
    # Set up Playwright to launch a browser
    async with async_playwright() as p:
        # headless=False lets you see the browser in action
        browser = await p.chromium.launch(headless=False) 
        context = await browser.new_context()
        page = await context.new_page()
        
        # Example coordinates (e.g., Central Park, NY) and category
        lat, lng = 40.785091, -73.968285
        category = "coffee shop"
        
        print(f"Scraping '{category}' near ({lat}, {lng})...")
        results = await scrape_grid_point(page, lat, lng, category)
        
        print(f"\nFound {len(results)} results:")
        
        # Format as GeoJSON
        geojson = {
            "type": "FeatureCollection",
            "features": []
        }
        
        for res in results:
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    # GeoJSON expects [longitude, latitude] order
                    "coordinates": [float(res["Longitude"]), float(res["Latitude"])]
                },
                "properties": {
                    "Name": res["Name"],
                    "Category": res["Category"]
                }
            }
            geojson["features"].append(feature)
            
        # Save to file
        filename = f"{category.replace(' ', '_')}_results.geojson"
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(geojson, f, indent=2, ensure_ascii=False)
            
        print(f"Saved results to {filename}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())