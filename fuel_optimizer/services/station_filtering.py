import math
from typing import List, Dict, Any, Optional
from fuel_optimizer.models import FuelStation

EARTH_RADIUS_MILES = 3958.8


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two geographic points in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_MILES * c


class StationFilterService:
    """
    Filters and projects fuel stations along an OSRM driving route geometry
    within a specified lateral corridor (default 5 miles).
    
    Approximation Disclosure:
    - `distance_from_route_miles` represents the perpendicular cross-track (Euclidean/Haversine)
      distance from the route polyline to the station coordinates, NOT an exact road driving detour.
    - Along-route station positions are projected onto the simplified route polyline and scaled
      proportionally to ensure strict mathematical consistency with OSRM's total driving distance.
    """

    @classmethod
    def filter_stations_along_route(
        cls,
        route_coordinates: List[List[float]],  # [[lon, lat], ...]
        corridor_miles: float = 5.0,
        total_route_distance_miles: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Identify fuel stations within corridor_miles of the route polyline.
        
        Args:
            route_coordinates: List of [longitude, latitude] pairs from OSRM GeoJSON geometry.
            corridor_miles: Maximum allowable perpendicular distance from route polyline (miles).
            total_route_distance_miles: Optional total driving distance reported by OSRM for normalization.
            
        Returns:
            List of station dicts sorted in strict travel order by 'distance_along_route_miles'.
        """
        if not route_coordinates or len(route_coordinates) < 2:
            return []

        # Convert GeoJSON [lon, lat] to [(lat, lon)]
        route_pts = [(pt[1], pt[0]) for pt in route_coordinates]
        num_pts = len(route_pts)

        # 1. Compute cumulative along-route distances for polyline vertices using Haversine
        cumulative_dist = [0.0] * num_pts
        for i in range(1, num_pts):
            prev_lat, prev_lon = route_pts[i - 1]
            curr_lat, curr_lon = route_pts[i]
            seg_dist = haversine_distance(prev_lat, prev_lon, curr_lat, curr_lon)
            cumulative_dist[i] = cumulative_dist[i - 1] + seg_dist

        poly_total_length = cumulative_dist[-1]

        # Scaling factor to align polyline Haversine sum with OSRM road distance
        scale_factor = 1.0
        if total_route_distance_miles is not None and total_route_distance_miles > 0 and poly_total_length > 0:
            scale_factor = total_route_distance_miles / poly_total_length

        max_allowed_dist = total_route_distance_miles if total_route_distance_miles else poly_total_length

        # 2. Compute bounding box for DB candidate pre-filtering to prevent full-table scans
        lats = [pt[0] for pt in route_pts]
        lons = [pt[1] for pt in route_pts]
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)

        # Buffer: corridor_miles converted to degree bounds (~69 miles per lat degree)
        lat_buffer = (corridor_miles / 60.0) + 0.1
        avg_lat_rad = math.radians((min_lat + max_lat) / 2.0)
        cos_lat = max(math.cos(avg_lat_rad), 0.1)
        lon_buffer = (corridor_miles / (60.0 * cos_lat)) + 0.1

        # Query stations in the bounding box
        candidate_stations = FuelStation.objects.filter(
            latitude__gte=min_lat - lat_buffer,
            latitude__lte=max_lat + lat_buffer,
            longitude__gte=min_lon - lon_buffer,
            longitude__lte=max_lon + lon_buffer,
        ).only(
            'id', 'station_id', 'station_name', 'address', 'city',
            'state', 'fuel_price', 'latitude', 'longitude', 'is_exact', 'geocode_source'
        )

        if not candidate_stations.exists():
            return []

        # 3. Simplify route vertices for efficient projection on long routes (step ~0.25 mi)
        simplified_indices = [0]
        step_threshold = 0.25  # miles
        last_added_idx = 0

        for i in range(1, num_pts - 1):
            if cumulative_dist[i] - cumulative_dist[last_added_idx] >= step_threshold:
                simplified_indices.append(i)
                last_added_idx = i
        simplified_indices.append(num_pts - 1)

        simplified_pts = [route_pts[idx] for idx in simplified_indices]
        simplified_cum_dist = [cumulative_dist[idx] for idx in simplified_indices]
        num_simp = len(simplified_pts)

        # Precalculate segment projection vectors in miles using equirectangular projection
        segments = []
        for i in range(num_simp - 1):
            lat_a, lon_a = simplified_pts[i]
            lat_b, lon_b = simplified_pts[i + 1]
            mid_lat_rad = math.radians((lat_a + lat_b) / 2.0)
            kx = 69.17 * math.cos(mid_lat_rad)
            ky = 69.17

            dx = (lon_b - lon_a) * kx
            dy = (lat_b - lat_a) * ky
            seg_len_sq = dx * dx + dy * dy

            segments.append({
                'lat_a': lat_a,
                'lon_a': lon_a,
                'kx': kx,
                'ky': ky,
                'dx': dx,
                'dy': dy,
                'seg_len_sq': seg_len_sq,
                'cum_start': simplified_cum_dist[i],
                'cum_end': simplified_cum_dist[i + 1],
                'seg_dist': simplified_cum_dist[i + 1] - simplified_cum_dist[i],
                'min_lat': min(lat_a, lat_b),
                'max_lat': max(lat_a, lat_b),
                'min_lon': min(lon_a, lon_b),
                'max_lon': max(lon_a, lon_b),
            })

        eligible_stations = []

        # 4. Project each candidate station onto closest segment
        for station in candidate_stations:
            s_lat = station.latitude
            s_lon = station.longitude

            min_cross_track = float('inf')
            best_along_dist = 0.0

            for seg in segments:
                # Fast rejection if outside segment bounding box
                if (s_lat < seg['min_lat'] - lat_buffer or s_lat > seg['max_lat'] + lat_buffer or
                        s_lon < seg['min_lon'] - lon_buffer or s_lon > seg['max_lon'] + lon_buffer):
                    continue

                # Local equirectangular projection
                wx = (s_lon - seg['lon_a']) * seg['kx']
                wy = (s_lat - seg['lat_a']) * seg['ky']

                if seg['seg_len_sq'] <= 1e-9:
                    t = 0.0
                else:
                    t = (wx * seg['dx'] + wy * seg['dy']) / seg['seg_len_sq']
                    t = max(0.0, min(1.0, t))

                proj_x = t * seg['dx']
                proj_y = t * seg['dy']
                dist_sq = (wx - proj_x) ** 2 + (wy - proj_y) ** 2

                if dist_sq < min_cross_track:
                    min_cross_track = dist_sq
                    best_along_dist = seg['cum_start'] + t * seg['seg_dist']

            cross_track_miles = math.sqrt(min_cross_track) if min_cross_track != float('inf') else float('inf')

            # Filter within lateral corridor
            if cross_track_miles <= corridor_miles:
                # Apply normalization factor to align with OSRM total distance
                normalized_along_dist = best_along_dist * scale_factor
                # Clamp within [0, max_allowed_dist]
                final_along_dist = round(min(max_allowed_dist, max(0.0, normalized_along_dist)), 2)

                eligible_stations.append({
                    'id': station.id,
                    'station_id': station.station_id,
                    'station_name': station.station_name,
                    'address': station.address,
                    'city': station.city,
                    'state': station.state,
                    'fuel_price': float(station.fuel_price),
                    'latitude': s_lat,
                    'longitude': s_lon,
                    'is_exact': station.is_exact,
                    'geocode_source': station.geocode_source,
                    'distance_from_route_miles': round(cross_track_miles, 2),
                    'distance_along_route_miles': final_along_dist,
                })

        # 5. Sort strictly in travel order along the route
        eligible_stations.sort(key=lambda x: x['distance_along_route_miles'])

        return eligible_stations
