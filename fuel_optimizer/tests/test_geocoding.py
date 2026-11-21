from unittest.mock import patch, MagicMock
from django.test import TestCase
from fuel_optimizer.models import GeocodeCache
from fuel_optimizer.services.geocoding import GeocodingService, GeocodingError


class GeocodingServiceTest(TestCase):

    def test_empty_address_raises_error(self):
        with self.assertRaises(GeocodingError):
            GeocodingService.geocode("   ")

    def test_database_cache_hit_avoids_network(self):
        GeocodeCache.objects.create(
            query="DALLAS, TX",
            latitude=32.7767,
            longitude=-96.7970,
            formatted_address="Dallas, Texas, United States",
            provider="cache_test"
        )

        with patch('requests.get') as mock_get:
            result = GeocodingService.geocode("Dallas, TX")
            mock_get.assert_not_called()
            self.assertTrue(result['cached'])
            self.assertEqual(result['latitude'], 32.7767)
            self.assertEqual(result['longitude'], -96.7970)

    @patch('requests.get')
    def test_successful_external_geocode_and_caching(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'lat': '39.7392',
            'lon': '-104.9903',
            'display_name': 'Denver, Colorado, United States',
            'address': {'country_code': 'us'}
        }]
        mock_get.return_value = mock_response

        result = GeocodingService.geocode("Denver, CO")
        self.assertFalse(result['cached'])
        self.assertEqual(result['latitude'], 39.7392)
        self.assertEqual(result['longitude'], -104.9903)

        # Check that it got saved into DB cache
        cached = GeocodeCache.objects.filter(query="DENVER, CO").first()
        self.assertIsNotNone(cached)
        self.assertEqual(cached.latitude, 39.7392)

    @patch('requests.get')
    def test_non_us_address_rejected(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'lat': '43.6532',
            'lon': '-79.3832',
            'display_name': 'Toronto, Ontario, Canada',
            'address': {'country_code': 'ca'}
        }]
        mock_get.return_value = mock_response

        with self.assertRaises(GeocodingError) as ctx:
            GeocodingService.geocode("Toronto, ON, Canada")
        self.assertIn("outside the United States", str(ctx.exception))

    @patch('requests.get')
    def test_unresolved_address_raises_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        with self.assertRaises(GeocodingError) as ctx:
            GeocodingService.geocode("NonExistentCityXYZ999")
        self.assertIn("Address not found", str(ctx.exception))
