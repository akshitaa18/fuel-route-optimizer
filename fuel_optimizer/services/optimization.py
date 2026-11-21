import math
from typing import List, Dict, Any, Optional, Tuple
from fuel_optimizer.models import FuelStation
from fuel_optimizer.services.station_filtering import haversine_distance


class OptimizationError(Exception):
    """Raised when route cannot be optimized or destination is unreachable."""
    pass


class FuelRouteOptimizer:
    """
    Graph-based Dynamic Programming cost optimizer for fuel stops.
    Determines the globally least-cost sequence of fuel purchases respecting
    the vehicle's maximum driving range.
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

        # 1. Determine starting fuel price (Assumption: vehicle starts empty and refuels at origin)
        origin_station = self._get_origin_refuel_station(start_coords, eligible_stations)
        origin_price = origin_station['fuel_price']

        # 2. Construct Graph Nodes:
        # Node 0: Origin
        # Nodes 1..M: Filtered eligible stations
        # Node M+1: Destination
        nodes = [{
            'node_id': 0,
            'type': 'origin',
            'name': f"Origin: {start_location}",
            'address': start_location,
            'city': '',
            'state': '',
            'latitude': start_coords[0],
            'longitude': start_coords[1],
            'fuel_price': origin_price,
            'distance_along_route_miles': 0.0,
            'station_id': origin_station.get('station_id'),
            'virtual_refuel_note': (
                f"Virtual starting refueling at cheapest nearby station: "
                f"{origin_station['name']} (${origin_price:.3f}/gal)"
            )
        }]

        # Add stations as nodes.
        # Ensure stations are between 0 and total_distance_miles.
        # To avoid graph clutter, deduplicate stations located at almost identical along-track positions.
        sorted_stations = sorted(eligible_stations, key=lambda s: s['distance_along_route_miles'])
        filtered_station_nodes = []
        last_dist = -1.0

        for st in sorted_stations:
            dist = st['distance_along_route_miles']
            # Skip stations beyond destination or behind origin
            if dist <= 0.0 or dist >= total_distance_miles:
                continue
            
            # If within 1.0 mile of previous station, only keep if cheaper
            if filtered_station_nodes and abs(dist - last_dist) < 1.0:
                if st['fuel_price'] < filtered_station_nodes[-1]['fuel_price']:
                    filtered_station_nodes[-1] = st
                    last_dist = dist
            else:
                filtered_station_nodes.append(st)
                last_dist = dist

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
                'fuel_price': st['fuel_price'],
                'distance_along_route_miles': st['distance_along_route_miles'],
                'distance_from_route_miles': st.get('distance_from_route_miles', 0.0),
                'station_id': st['station_id'],
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
            'fuel_price': 0.0,  # No fuel purchased at destination
            'distance_along_route_miles': total_distance_miles,
            'station_id': None,
        })

        num_nodes = len(nodes)

        # 3. Dynamic Programming
        # dp[i] = (min_cost_to_reach_node_i, num_stops_to_reach_node_i)
        # parent[i] = previous_node_index
        dp_cost = [float('inf')] * num_nodes
        dp_stops = [float('inf')] * num_nodes
        parent = [None] * num_nodes

        dp_cost[0] = 0.0
        dp_stops[0] = 0

        # Topological relaxation over the DAG
        for v in range(1, num_nodes):
            v_dist = nodes[v]['distance_along_route_miles']
            for u in range(v):
                u_dist = nodes[u]['distance_along_route_miles']
                seg_dist = v_dist - u_dist

                # Edge constraint: Driving distance between stops cannot exceed max_range
                if seg_dist <= 0:
                    continue
                if seg_dist > self.max_range:
                    continue

                if dp_cost[u] == float('inf'):
                    continue

                # Segment fuel required
                seg_gallons = seg_dist / self.mpg
                # Fuel purchased at station u
                seg_cost = seg_gallons * nodes[u]['fuel_price']

                candidate_cost = dp_cost[u] + seg_cost
                candidate_stops = dp_stops[u] + (1 if u > 0 else 0)

                # Minimize cost, with tie-break on fewer stops
                if candidate_cost < dp_cost[v] - 1e-6:
                    dp_cost[v] = candidate_cost
                    dp_stops[v] = candidate_stops
                    parent[v] = u
                elif abs(candidate_cost - dp_cost[v]) <= 1e-6 and candidate_stops < dp_stops[v]:
                    dp_stops[v] = candidate_stops
                    parent[v] = u

        # 4. Check if destination is reachable
        if dp_cost[dest_node_id] == float('inf'):
            # Diagnose where the range gap exists
            gap_info = self._diagnose_unreachable_gap(nodes)
            raise OptimizationError(
                f"Destination is unreachable within the {self.max_range}-mile driving range. "
                f"A maximum gap of {gap_info['gap_miles']:.1f} miles exists between "
                f"'{gap_info['from_node']}' and '{gap_info['to_node']}'. "
                f"No feasible refueling plan exists with the available stations in this corridor."
            )

        # 5. Reconstruct optimal path
        path = []
        curr = dest_node_id
        while curr is not None:
            path.append(curr)
            curr = parent[curr]
        path.reverse()

        # Build detailed stop breakdown
        stops = []
        total_money_spent = 0.0
        total_fuel_consumed = total_distance_miles / self.mpg

        for step_idx in range(len(path) - 1):
            u_idx = path[step_idx]
            v_idx = path[step_idx + 1]

            u_node = nodes[u_idx]
            v_node = nodes[v_idx]

            seg_distance = round(v_node['distance_along_route_miles'] - u_node['distance_along_route_miles'], 2)
            seg_gallons = round(seg_distance / self.mpg, 2)
            seg_cost = round(seg_gallons * u_node['fuel_price'], 2)
            total_money_spent += seg_cost

            # If node u is an actual station stop along the route (not origin)
            if u_node['type'] == 'fuel_stop':
                stops.append({
                    'stop_number': len(stops) + 1,
                    'station_id': u_node['station_id'],
                    'station_name': u_node['name'],
                    'address': u_node['address'],
                    'city': u_node['city'],
                    'state': u_node['state'],
                    'fuel_price': u_node['fuel_price'],
                    'distance_along_route_miles': round(u_node['distance_along_route_miles'], 2),
                    'distance_from_route_miles': u_node.get('distance_from_route_miles', 0.0),
                    'leg_distance_miles': seg_distance,
                    'fuel_added_gallons': seg_gallons,
                    'segment_cost_usd': seg_cost,
                    'coordinates': {
                        'latitude': u_node['latitude'],
                        'longitude': u_node['longitude'],
                    }
                })

        avg_price_per_gal = (
            round(total_money_spent / total_fuel_consumed, 3)
            if total_fuel_consumed > 0 else 0.0
        )

        return {
            'success': True,
            'route': {
                'start': start_location,
                'finish': finish_location,
                'distance_miles': round(total_distance_miles, 2),
                'duration_hours': round(duration_hours, 2),
                'geometry': route_geometry,
            },
            'fuel_summary': {
                'total_distance_miles': round(total_distance_miles, 2),
                'total_fuel_gallons': round(total_fuel_consumed, 2),
                'total_fuel_cost_usd': round(total_money_spent, 2),
                'num_stops': len(stops),
                'average_price_per_gallon': avg_price_per_gal,
            },
            'stops': stops,
            'origin_refuel': {
                'station_name': origin_station['name'],
                'fuel_price': origin_price,
                'note': nodes[0]['virtual_refuel_note'],
                'coordinates': {
                    'latitude': origin_station['latitude'],
                    'longitude': origin_station['longitude'],
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
                'algorithm': 'Graph-based Dynamic Programming (DAG Least Cost)',
            }
        }

    def _get_origin_refuel_station(
        self,
        start_coords: Tuple[float, float],
        eligible_stations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Find the cheapest fuel station within origin_refuel_radius miles of start coords.
        Falls back to nearest station or DB query if none in corridor.
        """
        lat, lon = start_coords
        radius_deg = self.origin_refuel_radius / 60.0

        # Query DB stations in the vicinity of origin
        nearby = FuelStation.objects.filter(
            latitude__gte=lat - radius_deg,
            latitude__lte=lat + radius_deg,
            longitude__gte=lon - radius_deg,
            longitude__lte=lon + radius_deg,
        )

        candidates = []
        for st in nearby:
            d = haversine_distance(lat, lon, st.latitude, st.longitude)
            if d <= self.origin_refuel_radius:
                candidates.append({
                    'station_id': st.station_id,
                    'name': st.station_name,
                    'fuel_price': float(st.fuel_price),
                    'latitude': st.latitude,
                    'longitude': st.longitude,
                    'distance': d
                })

        if candidates:
            # Sort primarily by cheapest fuel price
            candidates.sort(key=lambda x: (x['fuel_price'], x['distance']))
            return candidates[0]

        # Fallback 1: check eligible stations near origin (within first 50 miles)
        first_stations = [s for s in eligible_stations if s['distance_along_route_miles'] <= 50.0]
        if first_stations:
            first_stations.sort(key=lambda s: s['fuel_price'])
            return {
                'station_id': first_stations[0]['station_id'],
                'name': first_stations[0]['station_name'],
                'fuel_price': first_stations[0]['fuel_price'],
                'latitude': first_stations[0]['latitude'],
                'longitude': first_stations[0]['longitude'],
                'distance': first_stations[0]['distance_along_route_miles'],
            }

        # Fallback 2: nearest station in entire database
        cheapest_overall = FuelStation.objects.order_by('fuel_price').first()
        if cheapest_overall:
            return {
                'station_id': cheapest_overall.station_id,
                'name': cheapest_overall.station_name,
                'fuel_price': float(cheapest_overall.fuel_price),
                'latitude': cheapest_overall.latitude,
                'longitude': cheapest_overall.longitude,
                'distance': 0.0,
            }

        # Safe default if no DB stations
        return {
            'station_id': 0,
            'name': 'Default Origin Fuel Station',
            'fuel_price': 3.49,
            'latitude': lat,
            'longitude': lon,
            'distance': 0.0,
        }

    def _diagnose_unreachable_gap(self, nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Find the largest distance gap between adjacent reachable stations."""
        max_gap = 0.0
        from_node = "Origin"
        to_node = "Destination"

        for i in range(len(nodes) - 1):
            gap = nodes[i + 1]['distance_along_route_miles'] - nodes[i]['distance_along_route_miles']
            if gap > max_gap:
                max_gap = gap
                from_node = nodes[i]['name']
                to_node = nodes[i + 1]['name']

        return {
            'gap_miles': max_gap,
            'from_node': from_node,
            'to_node': to_node,
        }
