from unittest.mock import patch
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from fuel_optimizer.models import FuelStation, GeocodeCache


class ApiEndpointsTest(TestCase):

    def setUp(self):
        self.client = Client()
        # Seed a couple of stations
        FuelStation.objects.create(
            station_id=1,
            station_name="Pilot Travel Center #1",
            address="Exit 50",
            city="Columbus",
            state="OH",
            fuel_price=Decimal("3.0500"),
            latitude=39.9612,
            longitude=-82.9988,
        )
        FuelStation.objects.create(
            station_id=2,
            station_name="Love's Travel Stop #2",
            address="Exit 100",
            city="Dallas",
            state="TX",
            fuel_price=Decimal("2.8900"),
            latitude=32.7767,
            longitude=-96.7970,
        )

    def test_health_check_endpoint(self):
        url = reverse('fuel_optimizer:health')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'healthy')
        self.assertTrue(data['database']['connected'])
        self.assertEqual(data['database']['total_stations'], 2)

    def test_fuel_stations_list_and_filters(self):
        url = reverse('fuel_optimizer:fuel-stations')
        
        # Test full list
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 2)

        # Test state filter
        response_tx = self.client.get(f"{url}?state=TX")
        self.assertEqual(response_tx.status_code, 200)
        self.assertEqual(response_tx.json()['count'], 1)
        self.assertEqual(response_tx.json()['results'][0]['state'], 'TX')

        # Test price filter
        response_price = self.client.get(f"{url}?max_price=2.95")
        self.assertEqual(response_price.status_code, 200)
        self.assertEqual(response_price.json()['count'], 1)
        self.assertEqual(response_price.json()['results'][0]['station_id'], 2)

    def test_route_optimize_missing_fields_returns_400(self):
        url = reverse('fuel_optimizer:route-optimize')
        response = self.client.post(url, data={}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('details', data)

    @patch('fuel_optimizer.views.GeocodingService.geocode')
    @patch('fuel_optimizer.views.RoutingService.get_route')
    def test_route_optimize_success(self, mock_route, mock_geocode):
        mock_geocode.side_effect = [
            {'latitude': 40.7128, 'longitude': -74.0060, 'formatted_address': 'New York, NY, USA', 'cached': True},
            {'latitude': 39.9526, 'longitude': -75.1652, 'formatted_address': 'Philadelphia, PA, USA', 'cached': True},
        ]
        mock_route.return_value = {
            'distance_miles': 95.0,
            'duration_hours': 1.8,
            'geometry': {
                'type': 'LineString',
                'coordinates': [[-74.0060, 40.7128], [-74.5, 40.3], [-75.1652, 39.9526]]
            },
            'coordinates': [[-74.0060, 40.7128], [-74.5, 40.3], [-75.1652, 39.9526]],
            'cached': True,
        }

        url = reverse('fuel_optimizer:route-optimize')
        payload = {
            'start': 'New York, NY',
            'finish': 'Philadelphia, PA',
            'max_range': 500,
            'miles_per_gallon': 10,
            'corridor_miles': 5
        }
        response = self.client.post(url, data=payload, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['route']['distance_miles'], 95.0)
        self.assertEqual(data['fuel_summary']['total_fuel_gallons'], 9.5)
        self.assertIn('geometry', data['route'])
        self.assertIn('fuel_summary', data)
        self.assertIn('metadata', data)

    def test_frontend_map_view(self):
        url = reverse('map-frontend')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fuel Route Optimizer')
        self.assertContains(response, 'id="map"')

    def test_openapi_schema_endpoint(self):
        url = reverse('schema')
        response = self.client.get(f"{url}?format=json")
        self.assertEqual(response.status_code, 200)
        self.assertIn('openapi', response.json())
