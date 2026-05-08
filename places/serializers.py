from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer
from .models import Place

class PlaceSerializer(GeoFeatureModelSerializer):
    distance_m = serializers.SerializerMethodField()

    class Meta:
        model = Place
        geo_field = "location"
        fields = ['id', 'name', 'place_type', 'address', 'distance_m']

    def get_distance_m(self, obj):
        if hasattr(obj, 'distance'):
            return round(obj.distance.m, 2)
        return None