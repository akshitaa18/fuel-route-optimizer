import hashlib
import json
import logging
import time
import requests
from typing import Dict, Any, List, Tuple
from django.conf import settings
from fuel_optimizer.models import RouteCache

logger = logging.getLogger(__name__)

METERS_TO_MILES = 0.000621371
SECONDS_TO_HOURS = 1.0 / 3600.0


class RoutingError(Exception):
    """Raised when routing fails or external routing engine returns an error."""
    pass


class RoutingService:
    """
    Client for Open Source Routing Machine (OSRM) driving directions API.
    Provides route caching, retry handling, and conversion to miles and hours.
    """

    @classmethod
    def get_cache_key(cls, start_coords: Tuple[float, float], finish_coords: Tuple[float, float]) -> str:
        """Generate deterministic cache key for given start and finish coordinates."""
        key_str = f"{start_coords[1]:.5f},{start_coords[0]:.5f}->{finish_coords[1]:.5f},{finish_coords[0]:.5f}"
        return hashlib.sha256(key_str.encode('utf-8')).hexdigest()

    @classmethod
    def get_route(
        cls,
        start_coords: Tuple[float, float],  # (lat, lon)
        finish_coords: Tuple[float, float], # (lat, lon)
        start_label: str = "",
        finish_label: str = ""
    ) -> Dict[str, Any]:
        """
        Request driving route from OSRM between start_coords and finish_coords.
        Uses cached route if previously queried.
        
        Returns:
            dict with:
                - distance_miles (float)
                - duration_hours (float)
                - geometry (dict GeoJSON LineString)
                - coordinates (list of [lon, lat])
                - cached (bool)
        """
        cache_key = cls.get_cache_key(start_coords, finish_coords)

        # 1. Check persistent route cache
        cached_route = RouteCache.objects.filter(cache_key=cache_key).first()
        if cached_route:
            try:
                geometry = json.loads(cached_route.geometry_json)
                return {
                    'distance_miles': cached_route.distance_miles,
                    'duration_hours': cached_route.duration_hours,
                    'geometry': geometry,
                    'coordinates': geometry.get('coordinates', []),
                    'cached': True,
                }
            except json.JSONDecodeError:
                logger.warning("Corrupted route cache entry, re-fetching from OSRM.")

        # 2. Query external OSRM API with retry mechanism
        route_data = cls._fetch_osrm_route(start_coords, finish_coords)

        # 3. Store in persistent cache
        try:
            RouteCache.objects.update_or_create(
                cache_key=cache_key,
                defaults={
                    'start_location': start_label or f"{start_coords[0]:.4f},{start_coords[1]:.4f}",
                    'finish_location': finish_label or f"{finish_coords[0]:.4f},{finish_coords[1]:.4f}",
                    'distance_miles': route_data['distance_miles'],
                    'duration_hours': route_data['duration_hours'],
                    'geometry_json': json.dumps(route_data['geometry']),
                }
            )
        except Exception as e:
            logger.warning(f"Failed to persist route cache: {e}")

        route_data['cached'] = False
        return route_data

    @classmethod
    def _fetch_osrm_route(
        cls,
        start_coords: Tuple[float, float],
        finish_coords: Tuple[float, float],
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Execute HTTP request to OSRM server.
        OSRM format: /route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson
        """
        base_url = getattr(settings, 'OSRM_BASE_URL', 'http://router.project-osrm.org').rstrip('/')
        # Note: OSRM expects coordinates as longitude,latitude
        coords_str = f"{start_coords[1]},{start_coords[0]};{finish_coords[1]},{finish_coords[0]}"
        url = f"{base_url}/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
        timeout = getattr(settings, 'OSRM_TIMEOUT', 15)

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(url, timeout=timeout)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') != 'Ok':
                        code = data.get('code')
                        msg = data.get('message', 'Unknown routing error')
                        raise RoutingError(f"OSRM Routing failed with code '{code}': {msg}")

                    routes = data.get('routes', [])
                    if not routes:
                        raise RoutingError("No driving route found between the specified locations.")

                    primary_route = routes[0]
                    distance_meters = primary_route.get('distance', 0.0)
                    duration_seconds = primary_route.get('duration', 0.0)
                    geometry = primary_route.get('geometry', {})
                    coordinates = geometry.get('coordinates', [])

                    distance_miles = round(distance_meters * METERS_TO_MILES, 2)
                    duration_hours = round(duration_seconds * SECONDS_TO_HOURS, 2)

                    return {
                        'distance_miles': distance_miles,
                        'duration_hours': duration_hours,
                        'geometry': geometry,
                        'coordinates': coordinates,
                    }
                elif response.status_code in (429, 503, 504):
                    logger.warning(f"OSRM status {response.status_code} on attempt {attempt}. Retrying...")
                    time.sleep(1.0 * attempt)
                else:
                    raise RoutingError(f"OSRM returned HTTP {response.status_code}: {response.text}")

            except requests.RequestException as ex:
                logger.warning(f"OSRM connection failed on attempt {attempt}: {ex}")
                if attempt == max_retries:
                    raise RoutingError(f"Unable to connect to OSRM routing service: {ex}")
                time.sleep(0.5 * attempt)

        raise RoutingError(f"Routing request failed after {max_retries} attempts.")
