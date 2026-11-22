from decimal import Decimal
from django.test import TestCase
from fuel_optimizer.models import FuelStation
from fuel_optimizer.services.optimization import FuelRouteOptimizer, OptimizationError


class FuelRouteOptimizerTest(TestCase):

    def setUp(self):
        self.optimizer = FuelRouteOptimizer(
            max_range_miles=500.0,
            miles_per_gallon=10.0,
            tank_capacity_gallons=50.0,
            origin_refuel_radius_miles=25.0
        )
        # Standard seed origin station at NYC
        self.nyc_station = FuelStation.objects.create(
            station_id=901,
            station_name="NYC Origin Fuel Stop",
            address="100 West 33rd St",
            city="New York",
            state="NY",
            rack_id=1,
            fuel_price=Decimal("3.5000"),
            latitude=40.7128,
            longitude=-74.0060,
            is_exact=True,
            geocode_source='exact'
        )

    # -------------------------------------------------------------------------
    # Scenario 1: DP Outperforms Greedy Algorithm
    # -------------------------------------------------------------------------
    def test_dp_outperforms_greedy_approach(self):
        """
        Verify DP finds the global cost optimum where a greedy algorithm
        (choosing the cheapest locally reachable station) is tricked into an expensive route.
        """
        # Create station at origin (38.0, -88.0)
        FuelStation.objects.create(
            station_id=100,
            station_name="Midwest Origin Stop",
            address="1 Origin Way",
            city="Mount Vernon",
            state="IL",
            fuel_price=Decimal("3.5000"),
            latitude=38.0,
            longitude=-88.0,
            is_exact=True,
            geocode_source='exact'
        )

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
        # leg 1 (100 mi): 10 gal @ origin price ($3.50) = $35.00
        # leg 2 (490 mi): 49 gal @ $2.00 = $98.00
        # leg 3 (410 mi): 41 gal @ $5.00 = $205.00
        # Total Greedy cost = $338.00
        greedy_cost = (10.0 * 3.50) + (49.0 * 2.00) + (41.0 * 5.00)

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

    # -------------------------------------------------------------------------
    # Scenario 2: Destination Reachable Without Intermediate Stops
    # -------------------------------------------------------------------------
    def test_destination_reachable_without_intermediate_stops(self):
        """Routes <= 500 miles require 0 intermediate fuel stops when stations are more expensive."""
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
                    'fuel_price': 4.99,  # More expensive than origin refuel ($3.50)
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
        # 22.5 gal * $3.50 = $78.75
        self.assertEqual(result['fuel_summary']['total_fuel_cost_usd'], 78.75)
        self.assertEqual(result['origin_refuel']['initial_leg_distance_miles'], 225.0)
        self.assertEqual(result['origin_refuel']['initial_leg_fuel_gallons'], 22.5)
        self.assertEqual(result['origin_refuel']['initial_leg_cost_usd'], 78.75)

    # -------------------------------------------------------------------------
    # Scenario 3: Unreachable Range Gap Raises OptimizationError
    # -------------------------------------------------------------------------
    def test_unreachable_gap_raises_optimization_error(self):
        """When a distance gap between consecutive stations exceeds max_range, OptimizationError is raised."""
        FuelStation.objects.create(
            station_id=101,
            station_name="Desert Start Fuel",
            address="Main St",
            city="Ely",
            state="NV",
            fuel_price=Decimal("3.2000"),
            latitude=39.0,
            longitude=-114.0,
        )

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
        self.assertIn("gap", str(ctx.exception).lower())

    # -------------------------------------------------------------------------
    # Scenario 4: Station Exactly at Max Range Boundary (500 Miles)
    # -------------------------------------------------------------------------
    def test_station_exactly_at_max_range_boundary(self):
        """A station situated at exactly 500.0 miles must be considered feasible (<= max_range)."""
        eligible = [
            {
                'station_id': 500,
                'station_name': 'Boundary Station 500',
                'address': 'Mile 500 Marker',
                'city': 'Boundary City',
                'state': 'OH',
                'fuel_price': 3.00,
                'latitude': 40.0,
                'longitude': -80.0,
                'distance_along_route_miles': 500.0,  # Exactly at the 500-mile limit
            }
        ]

        result = self.optimizer.optimize_route(
            start_location="New York, NY",
            start_coords=(40.7128, -74.0060),
            finish_location="Midwest City",
            finish_coords=(40.0, -85.0),
            total_distance_miles=900.0,
            duration_hours=14.0,
            route_geometry={"type": "LineString", "coordinates": []},
            eligible_stations=eligible
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['fuel_summary']['num_stops'], 1)
        self.assertEqual(result['stops'][0]['station_id'], 500)
        self.assertEqual(result['stops'][0]['distance_along_route_miles'], 500.0)

    # -------------------------------------------------------------------------
    # Scenario 5: Multiple Stations at Nearly Identical Positions (Deduplication)
    # -------------------------------------------------------------------------
    def test_multiple_stations_at_nearly_identical_positions(self):
        """When multiple stations exist at the same mile marker, the cheaper one is chosen."""
        eligible = [
            {
                'station_id': 101,
                'station_name': 'Expensive Exit Station',
                'address': 'Exit 40 East',
                'city': 'Crossroads',
                'state': 'PA',
                'fuel_price': 3.80,
                'latitude': 40.5,
                'longitude': -76.0,
                'distance_along_route_miles': 300.0,
            },
            {
                'station_id': 102,
                'station_name': 'Cheaper Exit Station',
                'address': 'Exit 40 West',
                'city': 'Crossroads',
                'state': 'PA',
                'fuel_price': 2.75,
                'latitude': 40.5,
                'longitude': -76.01,
                'distance_along_route_miles': 300.2,
            }
        ]

        result = self.optimizer.optimize_route(
            start_location="New York, NY",
            start_coords=(40.7128, -74.0060),
            finish_location="Cleveland, OH",
            finish_coords=(41.4993, -81.6944),
            total_distance_miles=650.0,
            duration_hours=10.0,
            route_geometry={"type": "LineString", "coordinates": []},
            eligible_stations=eligible
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['fuel_summary']['num_stops'], 1)
        self.assertEqual(result['stops'][0]['station_id'], 102)
        self.assertEqual(result['stops'][0]['fuel_price'], 2.75)

    # -------------------------------------------------------------------------
    # Scenario 6: Initial Station Not Reachable (> Origin Radius)
    # -------------------------------------------------------------------------
    def test_initial_station_unreachable_raises_optimization_error(self):
        """When no station exists within origin_refuel_radius, an informative error is raised."""
        # Remote coords in Alaska where no station exists within 25 miles
        remote_coords = (65.0, -150.0)

        with self.assertRaises(OptimizationError) as ctx:
            self.optimizer.optimize_route(
                start_location="Remote Wilderness, AK",
                start_coords=remote_coords,
                finish_location="Fairbanks, AK",
                finish_coords=(64.8378, -147.7164),
                total_distance_miles=150.0,
                duration_hours=3.0,
                route_geometry={"type": "LineString", "coordinates": []},
                eligible_stations=[]
            )

        self.assertIn("No fuel station found within", str(ctx.exception))
        self.assertIn("initial fuel price", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Scenario 7: Fuel Cost Calculated Correctly and Fully Reconciled
    # -------------------------------------------------------------------------
    def test_fuel_cost_calculated_correctly_across_multiple_segments_decimal_precision(self):
        """
        Verify that total cost strictly matches the sum of initial leg cost + intermediate stop costs
        without IEEE 754 floating point rounding drift or component rounding discrepancies.
        """
        eligible = [
            {
                'station_id': 201,
                'station_name': 'Segment Stop 1',
                'address': 'I-80 Mile 240',
                'city': 'Bloomsburg',
                'state': 'PA',
                'fuel_price': 3.129,
                'latitude': 41.0,
                'longitude': -76.5,
                'distance_along_route_miles': 240.0,
            },
            {
                'station_id': 202,
                'station_name': 'Segment Stop 2',
                'address': 'I-80 Mile 580',
                'city': 'Youngstown',
                'state': 'OH',
                'fuel_price': 2.949,
                'latitude': 41.1,
                'longitude': -80.6,
                'distance_along_route_miles': 580.0,
            }
        ]

        result = self.optimizer.optimize_route(
            start_location="New York, NY",
            start_coords=(40.7128, -74.0060),
            finish_location="Chicago, IL",
            finish_coords=(41.8781, -87.6298),
            total_distance_miles=790.0,
            duration_hours=13.0,
            route_geometry={"type": "LineString", "coordinates": []},
            eligible_stations=eligible
        )

        self.assertTrue(result['success'])
        fuel_summary = result['fuel_summary']
        origin_refuel = result['origin_refuel']
        stops = result['stops']

        # Verify total gallons strictly equals initial leg gallons + sum of stop gallons
        sum_gallons = round(origin_refuel['initial_leg_fuel_gallons'] + sum(s['fuel_added_gallons'] for s in stops), 2)
        self.assertEqual(fuel_summary['total_fuel_gallons'], sum_gallons)

        # Monetary reconciliation: Total cost MUST strictly equal initial leg cost + sum of stop segment costs
        sum_costs = round(origin_refuel['initial_leg_cost_usd'] + sum(s['segment_cost_usd'] for s in stops), 2)
        self.assertEqual(fuel_summary['total_fuel_cost_usd'], sum_costs)

    # -------------------------------------------------------------------------
    # Scenario 8: Fewer Stops Chosen When Costs Are Equal (Exact Tie-Breaker)
    # -------------------------------------------------------------------------
    def test_route_uses_fewer_stops_when_costs_are_equal_tie_breaker(self):
        """When multiple valid paths have equal fuel cost, the route with fewer stops is preferred."""
        # Route is 800 miles. Max range is 500 miles.
        # Path 1 (2 stops): 0 -> 200 mi (Station 301) -> 500 mi (Station 303) -> 800 mi.
        # Path 2 (1 stop):  0 -> 400 mi (Station 302) -> 800 mi.
        # Both paths use fuel at $3.50/gal everywhere -> identical cost (80 gal * $3.50 = $280).
        # Exact tie-breaker MUST select Path 2 with 1 stop over Path 1 with 2 stops.
        eligible = [
            {
                'station_id': 301,
                'station_name': 'Two-Stop Candidate A',
                'address': 'Mile 200',
                'city': 'Town 1',
                'state': 'PA',
                'fuel_price': 3.50,
                'latitude': 40.5,
                'longitude': -77.0,
                'distance_along_route_miles': 200.0,
            },
            {
                'station_id': 302,
                'station_name': 'One-Stop Candidate (Tie Winner)',
                'address': 'Mile 400',
                'city': 'Town 2',
                'state': 'PA',
                'fuel_price': 3.50,
                'latitude': 40.5,
                'longitude': -78.5,
                'distance_along_route_miles': 400.0,
            },
            {
                'station_id': 303,
                'station_name': 'Two-Stop Candidate B',
                'address': 'Mile 500',
                'city': 'Town 3',
                'state': 'OH',
                'fuel_price': 3.50,
                'latitude': 40.5,
                'longitude': -80.0,
                'distance_along_route_miles': 500.0,
            }
        ]

        result = self.optimizer.optimize_route(
            start_location="New York, NY",
            start_coords=(40.7128, -74.0060),
            finish_location="Midwest Hub",
            finish_coords=(41.0, -84.0),
            total_distance_miles=800.0,
            duration_hours=12.5,
            route_geometry={"type": "LineString", "coordinates": []},
            eligible_stations=eligible
        )

        self.assertTrue(result['success'])
        # The 1-stop path must be selected over the 2-stop path
        self.assertEqual(result['fuel_summary']['num_stops'], 1)
        self.assertEqual(result['stops'][0]['station_id'], 302)

    # -------------------------------------------------------------------------
    # Scenario 9: Geographically Correct Bounding Box for Origin Station Search
    # -------------------------------------------------------------------------
    def test_geographically_correct_bounding_box(self):
        """
        Verify that origin station search correctly calculates latitude-adjusted longitude bounds
        and verifies straight-line distance.
        """
        # Place a station at latitude 60.0 degrees (cos(60) = 0.5, so 1 deg lon is only ~34.5 miles)
        # At lat 60.0, lon -140.0:
        # A station at lon -140.5 is 0.5 * 34.5 ~ 17.25 miles away (within 25 mi)
        FuelStation.objects.create(
            station_id=777,
            station_name="High Latitude Test Station",
            address="Highway 1",
            city="Anchorage Area",
            state="AK",
            rack_id=1,
            fuel_price=Decimal("3.4000"),
            latitude=60.0,
            longitude=-140.4,
            is_exact=True,
            geocode_source='exact'
        )

        station = self.optimizer._get_origin_refuel_station(
            start_coords=(60.0, -140.0),
            eligible_stations=[]
        )
        self.assertEqual(station['station_id'], 777)
        self.assertLessEqual(station['distance_to_origin_miles'], 25.0)
