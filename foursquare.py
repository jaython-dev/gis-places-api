import requests
import json
import time
import os
import sys
import re
from math import cos, radians

# =====================================================
# CONFIG
# =====================================================

# Load .env file if it exists
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                parts = line.strip().split("=", 1)
                if len(parts) == 2:
                    k, v = parts[0].strip(), parts[1].strip().strip('"').strip("'")
                    os.environ[k] = v

FSQ_API_KEY = os.environ.get("FSQ_API_KEY", "")


# Search Mode: "locality" (call-efficient search using area names) or "grid" (exhaustive search using coordinates)
SEARCH_MODE = os.environ.get("SEARCH_MODE", "locality").lower()

# Vadodara Bounding Box (Only used for grid mode)
MIN_LAT = 22.2400
MAX_LAT = 22.3700
MIN_LON = 73.1000
MAX_LON = 73.2500

GRID_STEP_KM = 1.0
SEARCH_RADIUS = 1000

OUTPUT_FILE = "vadodara_foursquare_pois.geojson"

BASE_URL = "https://places-api.foursquare.com/places/search"

# Auto-prefix Foursquare Studio/Places tokens with 'Bearer ' if not already formatted
auth_token = FSQ_API_KEY
if auth_token and not auth_token.startswith("Bearer "):
    auth_token = f"Bearer {auth_token}"

HEADERS = {
    "Authorization": auth_token,
    "X-Places-Api-Version": "2025-06-17",
    "Accept": "application/json"
}

# Categories to search (Restaurant, Cafe, Bar/Dining, Hotel, Hospital, Doctor/Clinic, School)
CATEGORIES = [
    "13065",  # Restaurant
    "13032",  # Cafe
    "13003",  # Bar / Dining and Drinking
    "19014",  # Hotel
    "17069",  # Hospital
    "11056",  # Doctor / Clinic / Health
    "15014",  # School
]

# =====================================================
# LOCALITIES GENERATOR
# =====================================================

def get_localities():
    default_localities = [
        "Alkapuri",
        "Fatehgunj",
        "Manjalpur",
        "Gotri",
        "Karelibaug",
        "Tandalja",
        "Vasna Road",
        "Akota",
        "Subhanpura",
        "Gorwa",
        "Nizampura",
        "Channi",
        "Waghodia Road",
        "Sama",
        "Harni",
        "Makarpura",
        "Pratapnagar",
        "Atladara",
        "Bhayli",
        "Diwalipura",
        "Ellora Park",
        "Sayajigunj",
        "Mandvi",
        "Panigate",
        "Wadi"
    ]
    default_localities.sort()
    return default_localities

# =====================================================
# GRID GENERATOR (Only used for grid mode)
# =====================================================

def generate_grid():
    lat_step = GRID_STEP_KM / 111.0
    lat = MIN_LAT

    while lat <= MAX_LAT:
        lon_step = GRID_STEP_KM / (111.0 * cos(radians(lat)))
        lon = MIN_LON
        while lon <= MAX_LON:
            yield round(lat, 6), round(lon, 6)
            lon += lon_step
        lat += lat_step

# =====================================================
# FETCH PLACES
# =====================================================

def fetch_places(lat=None, lon=None, locality=None, category=None):
    all_places = []

    # Use specified category if provided, otherwise fallback to all categories joined
    cats = category if category else ",".join(CATEGORIES)

    while True:
        if locality:
            params = {
                "near": f"{locality}, Vadodara, Gujarat, India",
                "limit": 50,
                "categories": cats
            }
        else:
            params = {
                "ll": f"{lat},{lon}",
                "radius": SEARCH_RADIUS,
                "limit": 50,
                "categories": cats
            }

        try:
            response = requests.get(
                BASE_URL,
                headers=HEADERS,
                params=params,
                timeout=30
            )

            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            all_places.extend(results)
            break

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                print(f"\n[!] Authentication Error: The Foursquare API key '{FSQ_API_KEY}' is invalid or unauthorized (401).")
                print("[!] Please set a valid FSQ_API_KEY (starting with 'fsq3_') in your .env file or environment.")
                sys.exit(1)
            else:
                loc_info = locality if locality else f"{lat},{lon}"
                print(f"HTTP Error at {loc_info}: {e}")
            break
        except Exception as e:
            loc_info = locality if locality else f"{lat},{lon}"
            print(f"Error at {loc_info}: {e}")
            break

    return all_places

# =====================================================
# CONVERT TO GEOJSON FEATURE
# =====================================================

def place_to_feature(place):

    geocodes = place.get("geocodes", {})
    main_geo = geocodes.get("main", {})

    lat = main_geo.get("latitude") or place.get("latitude")
    lon = main_geo.get("longitude") or place.get("longitude")

    if lat is None or lon is None:
        return None

    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [lon, lat]
        },
        "properties": {
            "fsq_place_id": place.get("fsq_place_id"),
            "name": place.get("name"),
            "address": place.get("location", {}).get(
                "formatted_address"
            ),
            "country": place.get("location", {}).get(
                "country"
            ),
            "region": place.get("location", {}).get(
                "region"
            ),
            "locality": place.get("location", {}).get(
                "locality"
            ),
            "postcode": place.get("location", {}).get(
                "postcode"
            ),
            "categories": ", ".join([
                c.get("name")
                for c in place.get("categories", [])
                if c.get("name")
            ]),
            "timezone": place.get("timezone"),
            "website": place.get("website"),
            "tel": place.get("tel"),
            "source": "Foursquare"
        }
    }

    return feature

# =====================================================
# MAIN
# =====================================================

def main():
    # A valid key can be fsq3_ Service Key or eyJ... Studio JWT token
    is_valid_key = (
        FSQ_API_KEY.startswith("fsq3_") or
        FSQ_API_KEY.startswith("eyJ") or
        FSQ_API_KEY.startswith("Bearer ")
    )
    if not FSQ_API_KEY or not is_valid_key:
        print("\n[!] Configuration Error: FSQ_API_KEY is not configured or is invalid.")
        print(f"[!] Current Key: '{FSQ_API_KEY[:20]}...'")
        print("[!] Foursquare Places API requires either:")
        print("    - A Foursquare Places API Service Key starting with 'fsq3_'")
        print("    - A Foursquare Studio Access Token starting with 'eyJ'")
        print("[!] Please set a valid key in your .env file.")
        sys.exit(1)

    unique_places = {}

    if SEARCH_MODE == "locality":
        localities = get_localities()
        print(f"Search Mode: LOCALITY-based (using Foursquare 'near' parameter)")
        print(f"Localities to search: {len(localities)}")
        print(f"Categories to query: {len(CATEGORIES)}")
        print("Starting collection...\n")

        for idx, locality in enumerate(localities, start=1):
            print(f"[{idx}/{len(localities)}] Searching near '{locality}'...")
            for category in CATEGORIES:
                places = fetch_places(locality=locality, category=category)
                for place in places:
                    fsq_id = place.get("fsq_place_id")
                    if not fsq_id:
                        continue
                    unique_places[fsq_id] = place
                time.sleep(0.25)
            print(f"   -> Accumulative Unique POIs: {len(unique_places)}")
    else:
        grid_points = list(generate_grid())
        print(f"Search Mode: GRID-based (using Foursquare 'll' coordinates)")
        print(f"Grid Points: {len(grid_points)}")
        print("Starting collection...\n")

        for idx, (lat, lon) in enumerate(grid_points, start=1):
            print(
                f"[{idx}/{len(grid_points)}] "
                f"Searching {lat},{lon}"
            )
            places = fetch_places(lat=lat, lon=lon)
            for place in places:
                fsq_id = place.get("fsq_place_id")
                if not fsq_id:
                    continue
                unique_places[fsq_id] = place
            print(f"Unique POIs: {len(unique_places)}")
            time.sleep(0.3)

    print("\nCreating GeoJSON...")

    features = []

    for place in unique_places.values():

        feature = place_to_feature(place)

        if feature:
            features.append(feature)

    geojson = {
        "type": "FeatureCollection",
        "features": features
    }

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            geojson,
            f,
            ensure_ascii=False,
            indent=2
        )

    print("\n===================================")
    print(f"Total Unique POIs : {len(features)}")
    print(f"GeoJSON Saved     : {OUTPUT_FILE}")
    print("===================================")

# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":
    main()