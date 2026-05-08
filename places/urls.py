from django.urls import path
from .views import NearbyPlacesView, RouteView

urlpatterns = [
    path('places/nearby', NearbyPlacesView.as_view()),
    path('route', RouteView.as_view()),
]