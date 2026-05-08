import time
import requests
from django.core.management.base import BaseCommand
from django.contrib.gis.geos import Point
from places.models import Place

OSM_TYPES = ['restaurant', 'hospital', 'school', 'park', 'hotel']

class Command(BaseCommand):
    help = 'Load places from OpenStreetMap via Overpass API'

    def handle(self, *args, **kwargs):
        # Option 1: Monaco (Default)
        lat, lng, radius = 43.73, 7.41, 5000
        
        # Option 2: Ahmedabad (Switch here if using India map)
        # lat, lng, radius = 23.0225, 72.5714, 5000

        for place_type in OSM_TYPES:
            self.stdout.write(f"Fetching {place_type}s...")
            query = f"""
            [out:json][timeout:60];
            node[amenity={place_type}](around:{radius},{lat},{lng});
            out body;
            """
            
            for attempt in range(3):
                try:
                    response = requests.post(
                        "https://overpass-api.de/api/interpreter",
                        data={'data': query},
                        headers={'User-Agent': 'GIS-Places-API-Script/1.0'},
                        timeout=65
                    )
                    response.raise_for_status()
                    data = response.json()

                    for el in data.get('elements', []):
                        name = el.get('tags', {}).get('name', 'Unnamed')
                        Place.objects.get_or_create(
                            name=name,
                            place_type=place_type,
                            location=Point(el['lon'], el['lat'])
                        )
                    break  # Success, exit retry loop
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Attempt {attempt + 1} failed for {place_type}: {e}"))
                    if attempt == 2:
                        self.stdout.write(self.style.ERROR(f"All attempts failed to fetch {place_type}."))
                    else:
                        time.sleep(5)
                
            time.sleep(3)

        self.stdout.write(self.style.SUCCESS("OSM data loaded!"))