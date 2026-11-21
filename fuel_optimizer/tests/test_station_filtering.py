from decimal import Decimal
from django.test import TestCase
from fuel_optimizer.models import FuelStation
from fuel_optimizer.services.station_filtering import StationFilterService, haversine_distance


class StationFilterServiceTest(TestCase):

    def setUp(self):
        # Create stations:
        # Route is along latitude 40.0, from lon -80.0 to -78.0 (~106 miles)
        # Station 1: close to route (lat 40.01, lon -79.5), ~0.7 miles away
        self.st1 = FuelStation.objects.create(
            station_id=1,
            station_name="Near Station",
            address="Exit 1",
            city="City A",
            state="PA",
            fuel_price=Decimal("3.20"),
            latitude=40.01,
            longitude=-79.5,
        )

        # Station 2: far from route (lat 40.5, lon -79.5), ~34 miles away
        self.st2 = FuelStation.objects.create(
            station_id=2,
            station_name="Far Station",
            address="Far Rd",
            city="City B",
            state="PA",
            fuel_price=Decimal("2.90"),
            latitude=40.5,
            longitude=-79.5,
        )

        # Station 3: near route towards the end (lat 40.02, lon -78.5)
        self.st3 = FuelStation.objects.create(
            station_id=3,
            station_name="End Station",
            address="Exit 2",
            city="City C",
            state="PA",
            fuel_price=Decimal("3.10"),
            latitude=40.02,
            longitude=-78.5,
        )

    def test_haversine_distance(self):
        # NYC to Philadelphia is ~80-85 miles straight line
        dist = haversine_distance(40.7128, -74.0060, 39.9526, -75.1652)
        self.assertAlmostEqual(dist, 80.5, delta=5.0)

    def test_corridor_filtering(self):
        # Route polyline: [-80.0, 40.0] -> [-79.0, 40.0] -> [-78.0, 40.0]
        route_coords = [
            [-80.0, 40.0],
            [-79.0, 40.0],
            [-78.0, 40.0]
        ]

        eligible = StationFilterService.filter_stations_along_route(route_coords, corridor_miles=5.0)

        station_ids = [s['station_id'] for s in eligible]
        self.assertIn(1, station_ids)
        self.assertIn(3, station_ids)
        self.assertNotIn(2, station_ids)  # 34 miles away, outside 5-mile corridor

        # Verify sorted by distance along route
        distances = [s['distance_along_route_miles'] for s in eligible]
        self.assertEqual(distances, sorted(distances))
        self.assertTrue(eligible[0]['distance_along_route_miles'] < eligible[1]['distance_along_route_miles'])
