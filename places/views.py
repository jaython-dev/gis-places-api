# pyrefly: ignore [missing-import]
from django.contrib.gis.geos import Point
from django.contrib.gis.db.models.functions import Distance
# pyrefly: ignore [missing-import]
from django.contrib.gis.measure import D
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.views.generic import TemplateView
from .models import Place
from .serializers import PlaceSerializer
import math
import requests

class MapView(TemplateView):
    template_name = 'places/map.html'

class NearbyPlacesView(APIView):
    def get(self, request):
        lat = request.GET.get('lat')
        lng = request.GET.get('lng')
        radius = request.GET.get('radius', 1000) 
        place_type = request.GET.get('type')

        # --- Validate inputs ---
        if not lat or not lng:
            return Response(
                {"error": "lat and lng are required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            lat, lng, radius = float(lat), float(lng), float(radius)
        except ValueError:
            return Response(
                {"error": "Invalid lat, lng, or radius"},
                status=status.HTTP_400_BAD_REQUEST
            )

        user_location = Point(lng, lat, srid=4326)

        places = Place.objects.filter(
            location__dwithin=(user_location, D(m=radius))
        ).annotate(
            distance=Distance('location', user_location)
        ).order_by('distance')

        if place_type:
            places = places.filter(place_type=place_type)

        if not places.exists():
            return Response({"message": "No places found", "results": []})

        serializer = PlaceSerializer(places, many=True, context={'user_location': user_location})
        return Response({"count": places.count(), "results": serializer.data})


class RouteView(APIView):
    def get(self, request):
        try:
            src_lat = float(request.GET.get('source_lat'))
            src_lng = float(request.GET.get('source_lng'))
            dst_lat = float(request.GET.get('dest_lat'))
            dst_lng = float(request.GET.get('dest_lng'))
        except (TypeError, ValueError):
            return Response(
                {"error": "All 4 coordinates are required and must be numbers"},
                status=status.HTTP_400_BAD_REQUEST
            )

        vehicle = request.GET.get('vehicle', 'car')
        if vehicle not in ['car', 'foot', 'bicycle']:
            vehicle = 'car'

        profile_map = {
            'car': 'driving',
            'foot': 'foot',
            'bicycle': 'bike'
        }
        osrm_profile = profile_map.get(vehicle, 'driving')

        # Use the specific container based on vehicle
        osrm_host = f"http://osrm-{vehicle}:5000"
        osrm_url = f"{osrm_host}/route/v1/{osrm_profile}/{src_lng},{src_lat};{dst_lng},{dst_lat}?overview=full&geometries=geojson"
        
        try:
            response = requests.get(osrm_url)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') != 'Ok':
                return Response({"error": "No route found"}, status=status.HTTP_404_NOT_FOUND)
                
            route = data['routes'][0]
            distance_m = route['distance']
            duration_sec = route['duration']
            geometry = route['geometry']
            
            return Response({
                "source": {"lat": src_lat, "lng": src_lng},
                "destination": {"lat": dst_lat, "lng": dst_lng},
                "total_distance_m": round(distance_m, 2),
                "total_distance_km": round(distance_m / 1000, 3),
                "estimated_duration_sec": round(duration_sec),
                "estimated_duration_min": round(duration_sec / 60, 1),
                "route_geometry": geometry
            })
        except requests.exceptions.RequestException as e:
            return Response({"error": f"Error connecting to OSRM: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)