from django.test import TestCase
from fuel_optimizer.services.optimization import FuelRouteOptimizer, OptimizationError


class FuelRouteOptimizerTest(TestCase):

    def setUp(self):
        self.optimizer = FuelRouteOptimizer(
            max_range_miles=500.0,
            miles_per_gallon=10.0,
            tank_capacity_gallons=50.0,
            origin_refuel_radius_miles=25.0
        )

    def test_short_route_under_500_miles_requires_zero_stops_when_stations_more_expensive(self):
        """When intermediate stations are more expensive than origin fuel, zero stops are made."""
        result = self.optimizer.optimize_route(
            start_location="New York, NY",
            start_coords=(40.7128, -74.0060),
            finish_location="Washington, DC",
            finish_coords=(38.9072, -77.0369),
            total_distance_miles=225.0,
            duration_hours=4.0,
            route_geometry={"type": "LineString", "coordinates": []},
            eligible_stations=[
                {
                    'station_id': 10,
                    'station_name': 'Expensive Midway Plaza',
                    'address': 'I-95 Exit 4',
                    'city': 'Newark',
                    'state': 'DE',
                    'fuel_price': 4.99,  # More expensive than origin refuel
                    'latitude': 39.6837,
                    'longitude': -75.7497,
                    'distance_along_route_miles': 110.0,
                }
            ]
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['fuel_summary']['num_stops'], 0)
        self.assertEqual(len(result['stops']), 0)
        self.assertEqual(result['fuel_summary']['total_distance_miles'], 225.0)
        self.assertEqual(result['fuel_summary']['total_fuel_gallons'], 22.5)

    def test_short_route_with_no_stations(self):
        """Routes under 500 miles with zero intermediate stations succeed without stops."""
        result = self.optimizer.optimize_route(
            start_location="Start",
            start_coords=(40.0, -75.0),
            finish_location="End",
            finish_coords=(40.0, -78.0),
            total_distance_miles=180.0,
            duration_hours=3.0,
            route_geometry={"type": "LineString", "coordinates": []},
            eligible_stations=[]
        )
        self.assertTrue(result['success'])
        self.assertEqual(result['fuel_summary']['num_stops'], 0)
        self.assertEqual(result['fuel_summary']['total_fuel_gallons'], 18.0)

    def test_long_route_selects_feasible_stops_within_range(self):
        """For a 900-mile route, stops must be chosen such that no segment exceeds 500 miles."""
        eligible_stations = [
            {
                'station_id': 1,
                'station_name': 'Stop A',
                'address': 'Hwy 1',
                'city': 'City A',
                'state': 'OH',
                'fuel_price': 3.10,
                'latitude': 41.0,
                'longitude': -82.0,
                'distance_along_route_miles': 300.0,
            },
            {
                'station_id': 2,
                'station_name': 'Stop B',
                'address': 'Hwy 2',
                'city': 'City B',
                'state': 'IN',
                'fuel_price': 3.25,
                'latitude': 41.0,
                'longitude': -85.0,
                'distance_along_route_miles': 450.0,
            },
            {
                'station_id': 3,
                'station_name': 'Stop C',
                'address': 'Hwy 3',
                'city': 'City C',
                'state': 'IL',
                'fuel_price': 2.95,
                'latitude': 41.0,
                'longitude': -89.0,
                'distance_along_route_miles': 700.0,
            }
        ]

        result = self.optimizer.optimize_route(
            start_location="Start City",
            start_coords=(41.0, -74.0),
            finish_location="End City",
            finish_coords=(41.0, -92.0),
            total_distance_miles=900.0,
            duration_hours=15.0,
            route_geometry={"type": "LineString", "coordinates": []},
            eligible_stations=eligible_stations
        )

        self.assertTrue(result['success'])
        self.assertTrue(result['fuel_summary']['num_stops'] >= 1)

        # Verify that distance between every consecutive stop is <= 500 miles
        prev_mile = 0.0
        for stop in result['stops']:
            leg = stop['distance_along_route_miles'] - prev_mile
            self.assertLessEqual(leg, 500.0)
            prev_mile = stop['distance_along_route_miles']

        final_leg = 900.0 - prev_mile
        self.assertLessEqual(final_leg, 500.0)

    def test_dp_outperforms_greedy_approach(self):
        """
        Verify DP finds the global cost optimum where a greedy algorithm
        (choosing the cheapest locally reachable station) is tricked into a globally expensive route.
        """
        eligible = [
            {
                'station_id': 1,
                'station_name': 'Greedy Trap (Locally Cheap)',
                'address': 'Hwy 10',
                'city': 'Trap City',
                'state': 'MO',
                'fuel_price': 2.00,
                'latitude': 38.0,
                'longitude': -90.0,
                'distance_along_route_miles': 100.0,
            },
            {
                'station_id': 2,
                'station_name': 'Expensive Forced Stop from Trap',
                'address': 'Hwy 10',
                'city': 'Pricey Town',
                'state': 'KS',
                'fuel_price': 5.00,
                'latitude': 38.0,
                'longitude': -95.0,
                'distance_along_route_miles': 590.0,
            },
            {
                'station_id': 3,
                'station_name': 'Balanced Optimal Stop 1',
                'address': 'Hwy 10',
                'city': 'Midway',
                'state': 'MO',
                'fuel_price': 2.80,
                'latitude': 38.0,
                'longitude': -92.0,
                'distance_along_route_miles': 350.0,
            },
            {
                'station_id': 4,
                'station_name': 'Balanced Optimal Stop 2',
                'address': 'Hwy 10',
                'city': 'West City',
                'state': 'CO',
                'fuel_price': 2.80,
                'latitude': 38.0,
                'longitude': -102.0,
                'distance_along_route_miles': 700.0,
            }
        ]

        # Greedy choice:
        # At 0: reachable are 1 (100 mi, $2.00) and 3 (350 mi, $2.80). Greedy picks Station 1 ($2.00).
        # At Station 1 (100 mi): can only reach up to 600 mi. Only Station 2 (590 mi, $5.00) is reachable.
        # At Station 2 (590 mi): reaches Destination (1000 mi, 410 mi leg).
        # Greedy costs:
        # leg 1 (100 mi): 10 gal @ origin price (~$3.49) = $34.90
        # leg 2 (490 mi): 49 gal @ $2.00 = $98.00
        # leg 3 (410 mi): 41 gal @ $5.00 = $205.00
        # Total Greedy cost = $337.90
        greedy_cost = (10.0 * 3.49) + (49.0 * 2.00) + (41.0 * 5.00)

        result = self.optimizer.optimize_route(
            start_location="Test Origin",
            start_coords=(38.0, -88.0),
            finish_location="Test Destination",
            finish_coords=(38.0, -105.0),
            total_distance_miles=1000.0,
            duration_hours=16.0,
            route_geometry={"type": "LineString", "coordinates": []},
            eligible_stations=eligible
        )

        dp_cost = result['fuel_summary']['total_fuel_cost_usd']
        self.assertLess(dp_cost, greedy_cost)
        
        # Verify DP chose the balanced optimal stations and avoided the expensive $5.00 station
        chosen_station_ids = [s['station_id'] for s in result['stops']]
        self.assertNotIn(2, chosen_station_ids)
        self.assertIn(3, chosen_station_ids)
        self.assertIn(4, chosen_station_ids)

    def test_unreachable_gap_raises_optimization_error(self):
        """When a gap exceeds max_range, an informative OptimizationError is raised."""
        eligible = [
            {
                'station_id': 1,
                'station_name': 'Lone Station',
                'address': 'Hwy 1',
                'city': 'Lonely',
                'state': 'NV',
                'fuel_price': 3.10,
                'latitude': 39.0,
                'longitude': -115.0,
                'distance_along_route_miles': 200.0,
            }
            # Gap from 200 to 1000 is 800 miles (> 500 max range)
        ]

        with self.assertRaises(OptimizationError) as ctx:
            self.optimizer.optimize_route(
                start_location="Desert Start",
                start_coords=(39.0, -114.0),
                finish_location="Desert End",
                finish_coords=(39.0, -120.0),
                total_distance_miles=1000.0,
                duration_hours=16.0,
                route_geometry={"type": "LineString", "coordinates": []},
                eligible_stations=eligible
            )

        self.assertIn("Destination is unreachable", str(ctx.exception))
        self.assertIn("gap", str(ctx.exception))
