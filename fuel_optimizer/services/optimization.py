import math
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any, Optional, Tuple
from fuel_optimizer.models import FuelStation
from fuel_optimizer.services.station_filtering import haversine_distance

# Conservative road circuity factor to approximate real road network distance from straight-line Haversine
ROAD_CIRCUITY_FACTOR = 1.2


class OptimizationError(Exception):
    """Raised when route cannot be optimized or destination is unreachable."""
    pass


class FuelRouteOptimizer:
    """
    Graph-based Dynamic Programming cost optimizer for fuel stops.
    Determines the globally least-cost sequence of fuel purchases respecting
    the vehicle's maximum driving range and physical tank capacity.
    
    Mathematical Formulation:
    - Origin Refueling: The vehicle starts with an empty tank. It must refuel at an initial
      station within `origin_refuel_radius_miles`. The detour distance to this station is
      conservatively modeled using the Haversine distance adjusted by a 1.2 road circuity factor.
    - Feasibility Constraints: Every driving segment (u, v) must satisfy:
        distance(u, v) <= max_range_miles
        gallons(u, v)  <= tank_capacity_gallons
    - Cost Minimization: Fuel for segment (u, v) is purchased at station u at price P_u:
        cost(u, v) = (distance(u, v) / MPG) * P_u
    - All intermediate arithmetic is calculated using Python Decimal to avoid binary float errors.
    """

    def __init__(
        self,
        max_range_miles: float = 500.0,
        miles_per_gallon: float = 10.0,
        tank_capacity_gallons: float = 50.0,
        origin_refuel_radius_miles: float = 25.0,
    ):
        self.max_range = float(max_range_miles)
        self.mpg = float(miles_per_gallon)
        self.tank_capacity = float(tank_capacity_gallons)
        self.origin_refuel_radius = float(origin_refuel_radius_miles)

    def optimize_route(
        self,
        start_location: str,
        start_coords: Tuple[float, float],       # (lat, lon)
        finish_location: str,
        finish_coords: Tuple[float, float],      # (lat, lon)
        total_distance_miles: float,
        duration_hours: float,
        route_geometry: Dict[str, Any],
        eligible_stations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Run Dynamic Programming optimization over the route and candidate stations.
        """
        if total_distance_miles <= 0:
            raise OptimizationError("Total route distance must be greater than zero.")

        if self.mpg <= 0:
            raise OptimizationError("Vehicle fuel efficiency (MPG) must be strictly positive.")

        if self.max_range <= 0:
            raise OptimizationError("Vehicle maximum range must be strictly positive.")

        if self.tank_capacity <= 0:
            raise OptimizationError("Vehicle tank capacity must be strictly positive.")

        # Physical consistency check: max range cannot exceed fuel tank capacity * MPG
        max_phys_range = self.tank_capacity * self.mpg
        if self.max_range > max_phys_range:
            raise OptimizationError(
                f"Configured max range ({self.max_range} miles) exceeds maximum physical range "
                f"achievable on a full tank ({self.tank_capacity} gal * {self.mpg} MPG = {max_phys_range} miles)."
            )

        # 1. Identify initial refueling station within origin radius
        origin_station_info = self._get_origin_refuel_station(start_coords, eligible_stations)
        origin_price_dec = Decimal(str(origin_station_info['fuel_price']))
        detour_dist_dec = Decimal(str(origin_station_info['detour_miles']))
        mpg_dec = Decimal(str(self.mpg))

        # Initial refuel detour cost: fuel to travel from origin to station
        detour_gallons_dec = detour_dist_dec / mpg_dec
        detour_cost_dec = detour_gallons_dec * origin_price_dec

        # 2. Construct Graph Nodes:
        # Node 0: Origin / Initial Refueling Station (at along-route position 0.0)
        nodes = [{
            'node_id': 0,
            'type': 'origin',
            'name': f"Initial Refuel: {origin_station_info['name']}",
            'address': origin_station_info.get('address', start_location),
            'city': origin_station_info.get('city', ''),
            'state': origin_station_info.get('state', ''),
            'latitude': origin_station_info['latitude'],
            'longitude': origin_station_info['longitude'],
            'fuel_price': origin_price_dec,
            'distance_along_route_miles': Decimal('0.0'),
            'station_id': origin_station_info.get('station_id'),
            'is_exact': origin_station_info.get('is_exact', False),
            'geocode_source': origin_station_info.get('geocode_source', 'city_centroid'),
            'detour_miles': detour_dist_dec,
            'detour_cost': detour_cost_dec,
            'detour_gallons': detour_gallons_dec,
        }]

        # Intermediate stations along the route
        sorted_stations = sorted(eligible_stations, key=lambda s: s['distance_along_route_miles'])
        filtered_station_nodes = []
        last_dist = Decimal('-1.0')

        for st in sorted_stations:
            dist_dec = Decimal(str(st['distance_along_route_miles']))
            # Discard stations behind origin or beyond destination
            if dist_dec <= Decimal('0.0') or dist_dec >= Decimal(str(total_distance_miles)):
                continue

            # To avoid graph clutter, merge stations within 1.0 mile of each other, keeping cheaper
            if filtered_station_nodes and abs(dist_dec - last_dist) < Decimal('1.0'):
                prev_price = filtered_station_nodes[-1]['fuel_price']
                curr_price = Decimal(str(st['fuel_price']))
                if curr_price < prev_price:
                    filtered_station_nodes[-1] = st
                    last_dist = dist_dec
            else:
                filtered_station_nodes.append(st)
                last_dist = dist_dec

        for idx, st in enumerate(filtered_station_nodes, start=1):
            nodes.append({
                'node_id': idx,
                'type': 'fuel_stop',
                'name': st['station_name'],
                'address': st['address'],
                'city': st['city'],
                'state': st['state'],
                'latitude': st['latitude'],
                'longitude': st['longitude'],
                'fuel_price': Decimal(str(st['fuel_price'])),
                'distance_along_route_miles': Decimal(str(st['distance_along_route_miles'])),
                'distance_from_route_miles': Decimal(str(st.get('distance_from_route_miles', 0.0))),
                'station_id': st['station_id'],
                'is_exact': st.get('is_exact', False),
                'geocode_source': st.get('geocode_source', 'city_centroid'),
            })

        # Destination node
        dest_node_id = len(nodes)
        nodes.append({
            'node_id': dest_node_id,
            'type': 'destination',
            'name': f"Destination: {finish_location}",
            'address': finish_location,
            'city': '',
            'state': '',
            'latitude': finish_coords[0],
            'longitude': finish_coords[1],
            'fuel_price': Decimal('0.0'),
            'distance_along_route_miles': Decimal(str(total_distance_miles)),
            'station_id': None,
        })

        num_nodes = len(nodes)
        max_range_dec = Decimal(str(self.max_range))
        tank_cap_dec = Decimal(str(self.tank_capacity))

        # 3. Dynamic Programming
        # dp_cost[i]: minimum cumulative fuel cost to reach node i from origin
        # dp_stops[i]: number of intermediate stops on the optimal path
        # parent[i]: predecessor node index
        dp_cost = [Decimal('Infinity')] * num_nodes
        dp_stops = [float('inf')] * num_nodes
        parent = [None] * num_nodes

        # Base case: initial refuel detour cost to arrive at origin station
        dp_cost[0] = detour_cost_dec
        dp_stops[0] = 0

        # Topological relaxation over the DAG
        for v in range(1, num_nodes):
            v_dist = nodes[v]['distance_along_route_miles']
            for u in range(v):
                u_dist = nodes[u]['distance_along_route_miles']
                seg_dist = v_dist - u_dist

                if seg_dist <= Decimal('0.0'):
                    continue

                # Range constraint: segment distance cannot exceed max vehicle range
                if seg_dist > max_range_dec:
                    continue

                # Tank capacity check: gallons needed cannot exceed tank capacity
                seg_gallons = seg_dist / mpg_dec
                if seg_gallons > tank_cap_dec:
                    continue

                if dp_cost[u] == Decimal('Infinity'):
                    continue

                # Fuel purchased at station u for segment (u, v)
                seg_cost = seg_gallons * nodes[u]['fuel_price']
                candidate_cost = dp_cost[u] + seg_cost
                candidate_stops = dp_stops[u] + (1 if u > 0 else 0)

                # Relax edge: minimize cost, with tie-break on fewer stops
                if candidate_cost < dp_cost[v] - Decimal('1e-6'):
                    dp_cost[v] = candidate_cost
                    dp_stops[v] = candidate_stops
                    parent[v] = u
                elif abs(candidate_cost - dp_cost[v]) <= Decimal('1e-6') and candidate_stops < dp_stops[v]:
                    dp_stops[v] = candidate_stops
                    parent[v] = u

        # 4. Check reachability of destination
        if dp_cost[dest_node_id] == Decimal('Infinity'):
            gap_info = self._diagnose_unreachable_gap(nodes)
            raise OptimizationError(
                f"Destination is unreachable within the {self.max_range}-mile driving range. "
                f"A maximum gap of {gap_info['gap_miles']:.1f} miles exists between "
                f"'{gap_info['from_node']}' and '{gap_info['to_node']}'. "
                f"No feasible refueling plan exists with the available stations in this corridor."
            )

        # 5. Reconstruct optimal stop sequence
        path = []
        curr = dest_node_id
        while curr is not None:
            path.append(curr)
            curr = parent[curr]
        path.reverse()

        stops = []
        total_money_spent_dec = detour_cost_dec
        route_dist_dec = Decimal(str(total_distance_miles))
        total_driving_distance_dec = route_dist_dec + detour_dist_dec
        total_fuel_consumed_dec = total_driving_distance_dec / mpg_dec

        initial_leg_distance_dec = Decimal('0.0')
        initial_leg_gallons_dec = Decimal('0.0')
        initial_leg_cost_dec = Decimal('0.0')

        for step_idx in range(len(path) - 1):
            u_idx = path[step_idx]
            v_idx = path[step_idx + 1]

            u_node = nodes[u_idx]
            v_node = nodes[v_idx]

            seg_distance = v_node['distance_along_route_miles'] - u_node['distance_along_route_miles']
            seg_gallons = seg_distance / mpg_dec
            seg_cost = seg_gallons * u_node['fuel_price']
            total_money_spent_dec += seg_cost

            if u_node['type'] == 'origin':
                initial_leg_distance_dec = seg_distance
                initial_leg_gallons_dec = seg_gallons
                initial_leg_cost_dec = seg_cost
            elif u_node['type'] == 'fuel_stop':
                stops.append({
                    'stop_number': len(stops) + 1,
                    'station_id': u_node['station_id'],
                    'station_name': u_node['name'],
                    'address': u_node['address'],
                    'city': u_node['city'],
                    'state': u_node['state'],
                    'fuel_price': float(u_node['fuel_price']),
                    'distance_along_route_miles': float(u_node['distance_along_route_miles'].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                    'distance_from_route_miles': float(u_node.get('distance_from_route_miles', Decimal('0.0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                    'leg_distance_miles': float(seg_distance.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                    'fuel_added_gallons': float(seg_gallons.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                    'segment_cost_usd': float(seg_cost.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                    'is_exact': u_node.get('is_exact', False),
                    'geocode_source': u_node.get('geocode_source', 'city_centroid'),
                    'coordinates': {
                        'latitude': u_node['latitude'],
                        'longitude': u_node['longitude'],
                    }
                })

        # Calculate average fuel cost per gallon
        avg_price_per_gal_dec = (
            total_money_spent_dec / total_fuel_consumed_dec
            if total_fuel_consumed_dec > Decimal('0.0') else Decimal('0.0')
        )

        total_cost_rounded = float(total_money_spent_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        total_fuel_rounded = float(total_fuel_consumed_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        avg_price_rounded = float(avg_price_per_gal_dec.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP))
        total_dist_rounded = float(total_driving_distance_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

        total_origin_gallons_dec = detour_gallons_dec + initial_leg_gallons_dec
        total_origin_cost_dec = detour_cost_dec + initial_leg_cost_dec

        return {
            'success': True,
            'route': {
                'start': start_location,
                'finish': finish_location,
                'distance_miles': round(total_distance_miles, 2),
                'total_driving_distance_miles': total_dist_rounded,
                'duration_hours': round(duration_hours, 2),
                'geometry': route_geometry,
            },
            'fuel_summary': {
                'total_distance_miles': total_dist_rounded,
                'route_distance_miles': round(total_distance_miles, 2),
                'total_fuel_gallons': total_fuel_rounded,
                'total_fuel_cost_usd': total_cost_rounded,
                'num_stops': len(stops),
                'average_price_per_gallon': avg_price_rounded,
            },
            'stops': stops,
            'origin_refuel': {
                'station_id': origin_station_info.get('station_id'),
                'station_name': origin_station_info['name'],
                'address': origin_station_info.get('address', ''),
                'city': origin_station_info.get('city', ''),
                'state': origin_station_info.get('state', ''),
                'fuel_price': float(origin_price_dec),
                'detour_distance_miles': float(detour_dist_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'detour_miles': float(detour_dist_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'detour_fuel_gallons': float(detour_gallons_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'detour_cost_usd': float(detour_cost_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'initial_leg_distance_miles': float(initial_leg_distance_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'initial_leg_fuel_gallons': float(initial_leg_gallons_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'initial_leg_cost_usd': float(initial_leg_cost_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'total_origin_gallons': float(total_origin_gallons_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'total_origin_cost_usd': float(total_origin_cost_dec.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
                'is_detour_modelled': True,
                'road_circuity_factor': ROAD_CIRCUITY_FACTOR,
                'is_exact': origin_station_info.get('is_exact', False),
                'geocode_source': origin_station_info.get('geocode_source', 'city_centroid'),
                'note': (
                    f"Empty-tank starting assumption: initial refueling at {origin_station_info['name']} "
                    f"(${float(origin_price_dec):.3f}/gal) located {float(detour_dist_dec):.1f} mi detour from origin."
                ),
                'coordinates': {
                    'latitude': origin_station_info['latitude'],
                    'longitude': origin_station_info['longitude'],
                }
            },
            'metadata': {
                'vehicle_specs': {
                    'max_range_miles': self.max_range,
                    'miles_per_gallon': self.mpg,
                    'tank_capacity_gallons': self.tank_capacity,
                },
                'corridor_stations_count': len(eligible_stations),
                'graph_nodes_evaluated': num_nodes,
                'algorithm': 'Graph-based Dynamic Programming (DAG Least Cost with Python Decimal)',
                'coordinate_precision_policy': (
                    'Includes verified city-centroid coordinates under conservative 5-mile corridor policy; '
                    'exact street addresses noted where available.'
                ),
                'detour_modeling_policy': (
                    'Initial detour distance uses Haversine distance from origin with a 1.2 road circuity factor. '
                    'Intermediate corridor stations use perpendicular cross-track projection.'
                ),
            }
        }

    def _get_origin_refuel_station(
        self,
        start_coords: Tuple[float, float],
        eligible_stations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Find the cheapest fuel station physically reachable within origin_refuel_radius miles of start coords.
        Does not perform wide-country fallback; if no station is within radius, raises OptimizationError.
        """
        lat, lon = start_coords
        radius_deg = self.origin_refuel_radius / 60.0

        # Query DB stations within bounding box
        nearby = FuelStation.objects.filter(
            latitude__gte=lat - radius_deg,
            latitude__lte=lat + radius_deg,
            longitude__gte=lon - radius_deg,
            longitude__lte=lon + radius_deg,
        )

        candidates = []
        for st in nearby:
            d_straight = haversine_distance(lat, lon, st.latitude, st.longitude)
            if d_straight <= self.origin_refuel_radius:
                d_road = round(d_straight * ROAD_CIRCUITY_FACTOR, 2)
                candidates.append({
                    'station_id': st.station_id,
                    'name': st.station_name,
                    'address': st.address,
                    'city': st.city,
                    'state': st.state,
                    'fuel_price': float(st.fuel_price),
                    'latitude': st.latitude,
                    'longitude': st.longitude,
                    'is_exact': st.is_exact,
                    'geocode_source': st.geocode_source,
                    'detour_miles': d_road,
                    'distance': d_straight
                })

        if candidates:
            # Sort primarily by lowest price, secondary by shortest detour
            candidates.sort(key=lambda x: (x['fuel_price'], x['detour_miles']))
            return candidates[0]

        # Check eligible corridor stations near origin (along-route <= origin_refuel_radius)
        first_stations = [s for s in eligible_stations if s['distance_along_route_miles'] <= self.origin_refuel_radius]
        if first_stations:
            first_stations.sort(key=lambda s: s['fuel_price'])
            st = first_stations[0]
            d_straight = haversine_distance(lat, lon, st['latitude'], st['longitude'])
            d_road = round(d_straight * ROAD_CIRCUITY_FACTOR, 2)
            return {
                'station_id': st['station_id'],
                'name': st['station_name'],
                'address': st.get('address', ''),
                'city': st.get('city', ''),
                'state': st.get('state', ''),
                'fuel_price': st['fuel_price'],
                'latitude': st['latitude'],
                'longitude': st['longitude'],
                'is_exact': st.get('is_exact', False),
                'geocode_source': st.get('geocode_source', 'city_centroid'),
                'detour_miles': d_road,
                'distance': d_straight,
            }

        # If no station exists within origin_refuel_radius, raise OptimizationError
        raise OptimizationError(
            f"No eligible fuel station found within the {self.origin_refuel_radius}-mile origin search radius. "
            f"Under the empty-tank assumption, the vehicle cannot refuel to begin the journey."
        )

    def _diagnose_unreachable_gap(self, nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Find the largest distance gap between adjacent reachable stations."""
        max_gap = Decimal('0.0')
        from_node = "Origin"
        to_node = "Destination"

        for i in range(len(nodes) - 1):
            gap = nodes[i + 1]['distance_along_route_miles'] - nodes[i]['distance_along_route_miles']
            if gap > max_gap:
                max_gap = gap
                from_node = nodes[i]['name']
                to_node = nodes[i + 1]['name']

        return {
            'gap_miles': float(max_gap),
            'from_node': from_node,
            'to_node': to_node,
        }
