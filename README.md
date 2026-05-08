# GIS Places API

A Django REST Framework API for managing geographical places and computing routes.

## Features
- **PostGIS Spatial Queries:** Utilizes `ST_DWithin` for highly optimized geographic distance calculations.
- **GeoJSON Formatting:** API responses are strictly formatted as RFC 7946 compliant GeoJSON `FeatureCollection` objects using `djangorestframework-gis`.
- **Real-World Routing:** Integrates an Open Source Routing Machine (OSRM) Docker container to calculate accurate driving routes, distances, and ETAs based on actual street networks.

## Prerequisites
- Docker & Docker Compose
- Python 3.9+
- A `.env` file (copy from `.env.example`)

## Setup Instructions

### 1. Start Docker Services
Initialize the PostGIS database and OSRM routing server:
```bash
docker-compose up -d
```
*(Note: By default, the `docker-compose.yml` is set to download **Monaco** (500KB) for fast local testing. If you want to use the full **India** map (1.5GB), you can switch the commented lines in the `docker-compose.yml` file, but please ensure your Docker VM has at least 16GB of RAM assigned.)*

### 2. Set Up Python Environment
```bash
python -m venv venv
# Windows
.\venv\Scripts\activate  
# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Apply Database Migrations
Create the PostGIS tables:
```bash
python manage.py makemigrations
python manage.py migrate
```

### 4. Populate Data
Extract Points of Interest (POIs) from the OSM map data and load them into the Django database to enable spatial search:
```bash
python manage.py load_osm_data
```

### 5. Start the Application
```bash
python manage.py runserver
```

## API Endpoints

### 1. Nearby Places
Searches for POIs within a specified radius (in meters) using PostGIS spatial queries.
- **Endpoint:** `GET /api/places/nearby?lat={lat}&lng={lng}&radius={radius}&type={type}`
- **Example (Monaco):** `http://localhost:8000/api/places/nearby?lat=43.73&lng=7.41&radius=3000`
- **Returns:** GeoJSON `FeatureCollection`

### 2. Route Calculation
Returns the exact routing geometry, total driving distance, and estimated duration between two coordinates via OSRM.
- **Endpoint:** `GET /api/route?source_lat={lat}&source_lng={lng}&dest_lat={lat}&dest_lng={lng}`
- **Example (Monaco):** `http://localhost:8000/api/route?source_lat=43.73&source_lng=7.41&dest_lat=43.74&dest_lng=7.42`
- **Returns:** GeoJSON `LineString` representing the driving path.
