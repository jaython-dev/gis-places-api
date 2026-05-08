from django.contrib.gis.db import models


class Place(models.Model):
    PLACE_TYPES = [
        ("restaurant", "Restaurant"),
        ("hospital", "Hospital"),
        ("school", "School"),
        ("park", "Park"),
        ("hotel", "Hotel"),
    ]

    name = models.CharField(max_length=255)
    place_type = models.CharField(max_length=100, choices=PLACE_TYPES)
    location = models.PointField(geography=True)
    address = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} ({self.place_type})"
