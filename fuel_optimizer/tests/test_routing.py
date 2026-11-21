import json
from unittest.mock import patch, MagicMock
from django.test import TestCase
from fuel_optimizer.models import RouteCache
from fuel_optimizer.services.routing import RoutingService, RoutingError


class RoutingServiceTest(TestCase):

    def test_cache_key_generation(self):
        k1 = RoutingService.get_cache_key((40.7128, -74.0060), (34.0522, -118.2437))
        k2 = RoutingService.get_cache_key((40.7128, -74.0060), (34.0522, -118.2437))
        self.assertEqual(k1, k2)

    def test_route_cache_hit_avoids_network(self):
        key = RoutingService.get_cache_key((40.0, -75.0), (41.0, -76.0))
        geometry = {"type": "LineString", "coordinates": [[-75.0, 40.0], [-76.0, 41.0]]}
        RouteCache.objects.create(
            cache_key=key,
            start_location="Point A",
            finish_location="Point B",
            distance_miles=85.5,
            duration_hours=1.5,
            geometry_json=json.dumps(geometry)
        )

        with patch('requests.get') as mock_get:
            result = RoutingService.get_route((40.0, -75.0), (41.0, -76.0))
            mock_get.assert_not_called()
            self.assertTrue(result['cached'])
            self.assertEqual(result['distance_miles'], 85.5)
            self.assertEqual(result['duration_hours'], 1.5)
            self.assertEqual(len(result['coordinates']), 2)

    @patch('requests.get')
    def test_successful_osrm_request(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "Ok",
            "routes": [{
                "distance": 160934.4,  # ~100 miles
                "duration": 7200.0,     # 2 hours
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[-74.0, 40.0], [-74.5, 40.5], [-75.0, 41.0]]
                }
            }]
        }
        mock_get.return_value = mock_response

        result = RoutingService.get_route((40.0, -74.0), (41.0, -75.0))
        self.assertFalse(result['cached'])
        self.assertEqual(result['distance_miles'], 100.0)
        self.assertEqual(result['duration_hours'], 2.0)
        self.assertEqual(len(result['coordinates']), 3)

        # Confirm saved to RouteCache
        key = RoutingService.get_cache_key((40.0, -74.0), (41.0, -75.0))
        self.assertTrue(RouteCache.objects.filter(cache_key=key).exists())

    @patch('requests.get')
    def test_osrm_error_code_raises_exception(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": "NoRoute",
            "message": "Impossible route"
        }
        mock_get.return_value = mock_response

        with self.assertRaises(RoutingError) as ctx:
            RoutingService.get_route((40.0, -74.0), (41.0, -75.0))
        self.assertIn("NoRoute", str(ctx.exception))
