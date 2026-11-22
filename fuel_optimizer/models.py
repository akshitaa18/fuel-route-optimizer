from django.db import models


class FuelStation(models.Model):
    """
    Fuel station model storing validated retail fuel price and geographic coordinates.
    Compatible with SQLite, PostgreSQL, and other relational databases.
    """
    station_id = models.IntegerField(db_index=True, help_text="OPIS Truckstop ID")
    station_name = models.CharField(max_length=255, help_text="Truckstop brand or facility name")
    address = models.CharField(max_length=255, help_text="Street address / highway exit")
    city = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=10, db_index=True, help_text="Two-letter US state code")
    rack_id = models.IntegerField(null=True, blank=True, help_text="Rack ID from OPIS feed")
    fuel_price = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        db_index=True,
        help_text="Retail diesel/fuel price in USD per gallon"
    )
    latitude = models.FloatField(db_index=True)
    longitude = models.FloatField(db_index=True)
    is_exact = models.BooleanField(
        default=False,
        help_text="True if resolved to exact street address, False if approximate city centroid"
    )
    geocode_source = models.CharField(
        max_length=50,
        default='city_centroid',
        help_text="Geocoding source or precision (e.g., exact_address, city_centroid, offline_gazetteer)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'fuel_stations'
        ordering = ['fuel_price']
        indexes = [
            models.Index(fields=['latitude', 'longitude'], name='idx_station_coords'),
            models.Index(fields=['state', 'fuel_price'], name='idx_station_state_price'),
            models.Index(fields=['station_id'], name='idx_station_opis_id'),
            models.Index(fields=['is_exact'], name='idx_station_is_exact'),
        ]

    def __str__(self):
        precision = "exact" if self.is_exact else "approx"
        return f"{self.station_name} ({self.city}, {self.state}) - ${self.fuel_price}/gal [{precision}]"


class GeocodeCache(models.Model):
    """
    Permanent cache for geocoded addresses and query strings.
    Ensures that identical addresses are never repeatedly geocoded across requests.
    """
    query = models.CharField(max_length=255, unique=True, db_index=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    formatted_address = models.CharField(max_length=255, blank=True)
    provider = models.CharField(max_length=50, default='census')
    is_exact = models.BooleanField(
        default=False,
        help_text="True if full street address match, False if city/state centroid"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'geocode_cache'

    def __str__(self):
        precision = "exact" if self.is_exact else "approx"
        return f"{self.query} -> ({self.latitude}, {self.longitude}) [{precision}]"


class RouteCache(models.Model):
    """
    Persistent cache for routing requests between start and destination coordinates.
    Avoids duplicate external OSRM HTTP calls for identical route requests.
    """
    cache_key = models.CharField(max_length=64, unique=True, db_index=True)
    start_location = models.CharField(max_length=255)
    finish_location = models.CharField(max_length=255)
    distance_miles = models.FloatField()
    duration_hours = models.FloatField()
    geometry_json = models.TextField(help_text="GeoJSON route geometry")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'route_cache'

    def __str__(self):
        return f"{self.start_location} -> {self.finish_location} ({self.distance_miles:.1f} mi)"
