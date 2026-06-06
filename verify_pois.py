import json
import sys

# Configure the target bounding box (matching the scraper's BBOX)
BBOX = (
    22.30602315776565,  # minLat
    73.1616261257188,   # minLon
    22.32123897140427,  # maxLat
    73.18153294773111   # maxLon
)

def verify_geojson(filepath, bbox_buffer=0.0):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading file: {e}")
        return

    features = data.get("features", [])
    total = len(features)
    print(f"==========================================")
    print(f"Verifying: {filepath}")
    print(f"Total POIs extracted: {total}")
    print(f"Target BBOX limits: Lat [{BBOX[0]}, {BBOX[2]}] | Lon [{BBOX[1]}, {BBOX[3]}]")
    print(f"==========================================")

    if total == 0:
        print("No features found to verify.")
        return

    out_of_bounds = []
    min_lat, max_lat = float('inf'), float('-inf')
    min_lon, max_lon = float('inf'), float('-inf')

    min_b_lat, min_b_lon, max_b_lat, max_b_lon = BBOX
    min_b_lat -= bbox_buffer
    max_b_lat += bbox_buffer
    min_b_lon -= bbox_buffer
    max_b_lon += bbox_buffer

    for feat in features:
        name = feat["properties"].get("name", "Unknown")
        lon, lat = feat["geometry"]["coordinates"]
        
        min_lat = min(min_lat, lat)
        max_lat = max(max_lat, lat)
        min_lon = min(min_lon, lon)
        max_lon = max(max_lon, lon)

        is_valid = (min_b_lat <= lat <= max_b_lat) and (min_b_lon <= lon <= max_b_lon)
        if not is_valid:
            out_of_bounds.append((name, lat, lon))

    print(f"Actual Extracted Range:")
    print(f"  Latitude:  [{min_lat:.6f} to {max_lat:.6f}]")
    print(f"  Longitude: [{min_lon:.6f} to {max_lon:.6f}]")
    print(f"------------------------------------------")

    if out_of_bounds:
        print(f"❌ FAILED: Found {len(out_of_bounds)} out-of-bounds POIs:")
        for name, lat, lon in out_of_bounds:
            print(f"   - {name} ({lat:.6f}, {lon:.6f})")
    else:
        print(f"✅ PASSED: All POIs are within the bounding box (buffer={bbox_buffer}).")
    print(f"==========================================")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python verify_pois.py <path_to_geojson_file> [optional_buffer]")
        sys.exit(1)
        
    file_to_check = sys.argv[1]
    buf = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    verify_geojson(file_to_check, bbox_buffer=buf)
