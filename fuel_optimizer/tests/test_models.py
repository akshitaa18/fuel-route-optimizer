import pytest
from decimal import Decimal
from django.test import TestCase
from fuel_optimizer.models import FuelStation, GeocodeCache, RouteCache


class FuelStationModelTest(TestCase):
    def setUp(self):
        self.station = FuelStation.objects.create(
            station_id=101,
            station_name="Pilot Travel Center #101",
            address="123 Highway 40",
            city="Amarillo",
            state="TX",
            rack_id=450,
            fuel_price=Decimal("3.1590"),
            latitude=35.2220,
            longitude=-101.8313,
        )

    def test_station_creation_and_str(self):
        self.assertEqual(self.station.station_id, 101)
        self.assertEqual(self.station.state, "TX")
        self.assertEqual(str(self.station), "Pilot Travel Center #101 (Amarillo, TX) - $3.1590/gal")

    def test_decimal_precision(self):
        self.assertEqual(float(self.station.fuel_price), 3.159)

    def test_geocode_cache_creation(self):
        cache_entry = GeocodeCache.objects.create(
            query="NEW YORK, NY",
            latitude=40.7128,
            longitude=-74.0060,
            formatted_address="New York, NY, USA",
            provider="test_provider"
        )
        self.assertEqual(str(cache_entry), "NEW YORK, NY -> (40.7128, -74.006)")

    def test_route_cache_creation(self):
        route = RouteCache.objects.create(
            cache_key="test_hash_12345",
            start_location="New York, NY",
            finish_location="Boston, MA",
            distance_miles=215.4,
            duration_hours=4.2,
            geometry_json='{"type": "LineString", "coordinates": [[-74.0, 40.7], [-71.0, 42.3]]}'
        )
        self.assertIn("New York, NY -> Boston, MA (215.4 mi)", str(route))
