import logging
import time
import requests
from typing import Tuple, Dict, Any, Optional
from django.conf import settings
from fuel_optimizer.models import GeocodeCache

logger = logging.getLogger(__name__)

# US Geographic Boundaries (Continental US + AK + HI + PR)
US_BOUNDS = {
    'min_lat': 18.0,
    'max_lat': 72.0,
    'min_lon': -180.0,
    'max_lon': -65.0,
}

class GeocodingError(Exception):
    """Custom exception raised when geocoding fails or location is invalid."""
    pass


class GeocodingService:
    """
    Geocoding service with multi-level caching (in-memory + database)
    and validation for US locations.
    """
    NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
    USER_AGENT = "FuelRouteOptimizerApp/1.0 (assessment@internal.app)"

    @classmethod
    def is_within_us(cls, lat: float, lon: float) -> bool:
        """Verify that coordinates fall within the United States bounding envelope."""
        return (
            US_BOUNDS['min_lat'] <= lat <= US_BOUNDS['max_lat'] and
            US_BOUNDS['min_lon'] <= lon <= US_BOUNDS['max_lon']
        )

    @classmethod
    def geocode(cls, address: str) -> Dict[str, Any]:
        """
        Geocode a location query string to latitude and longitude.
        Checks database cache first, then calls external service if necessary.
        Validates that location is within the USA.
        
        Returns:
            dict containing: 'latitude', 'longitude', 'formatted_address', 'cached'
        """
        cleaned_query = address.strip()
        if not cleaned_query:
            raise GeocodingError("Address cannot be empty.")

        cache_key = cleaned_query.upper()

        # 1. Check permanent database cache
        cached_entry = GeocodeCache.objects.filter(query=cache_key).first()
        if cached_entry:
            if not cls.is_within_us(cached_entry.latitude, cached_entry.longitude):
                raise GeocodingError(
                    f"Location '{address}' resolves to coordinates outside the United States: "
                    f"({cached_entry.latitude}, {cached_entry.longitude})."
                )
            return {
                'latitude': cached_entry.latitude,
                'longitude': cached_entry.longitude,
                'formatted_address': cached_entry.formatted_address or cleaned_query,
                'cached': True,
                'provider': cached_entry.provider,
            }

        # 2. Query external geocoding service with retries
        result = cls._query_external(cleaned_query)

        # 3. Validate US coordinates
        lat = result['latitude']
        lon = result['longitude']
        if not cls.is_within_us(lat, lon):
            raise GeocodingError(
                f"Location '{address}' resolves to coordinates outside the United States: ({lat}, {lon}). "
                "Only US locations are supported."
            )

        # 4. Save to permanent database cache
        try:
            GeocodeCache.objects.update_or_create(
                query=cache_key,
                defaults={
                    'latitude': lat,
                    'longitude': lon,
                    'formatted_address': result['formatted_address'],
                    'provider': result.get('provider', 'nominatim'),
                }
            )
        except Exception as e:
            logger.warning(f"Failed to persist geocode to DB cache: {e}")

        result['cached'] = False
        return result

    @classmethod
    def _query_external(cls, query: str, max_retries: int = 3) -> Dict[str, Any]:
        """Query Nominatim with retry logic and timeout."""
        headers = {'User-Agent': cls.USER_AGENT}
        params = {
            'q': query,
            'format': 'json',
            'addressdetails': 1,
            'limit': 1,
        }

        timeout = getattr(settings, 'GEOCODING_TIMEOUT', 10)

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(
                    cls.NOMINATIM_URL,
                    params=params,
                    headers=headers,
                    timeout=timeout
                )
                if response.status_code == 200:
                    data = response.json()
                    if not data or len(data) == 0:
                        raise GeocodingError(f"Address not found: '{query}'. Please check the location spelling.")

                    item = data[0]
                    lat = float(item['lat'])
                    lon = float(item['lon'])
                    display_name = item.get('display_name', query)
                    address_dict = item.get('address', {})
                    country_code = address_dict.get('country_code', '').lower()

                    if country_code and country_code != 'us':
                        raise GeocodingError(
                            f"Location '{query}' is in '{country_code.upper()}', outside the United States. "
                            "Only US locations are supported."
                        )

                    return {
                        'latitude': lat,
                        'longitude': lon,
                        'formatted_address': display_name,
                        'provider': 'nominatim',
                    }
                elif response.status_code == 429:
                    logger.warning(f"Rate limited by geocoding service on attempt {attempt}. Retrying...")
                    time.sleep(1.0 * attempt)
                else:
                    logger.warning(f"Geocoding HTTP error {response.status_code} on attempt {attempt}")
                    time.sleep(0.5 * attempt)

            except requests.RequestException as e:
                logger.warning(f"Geocoding connection error on attempt {attempt}: {e}")
                if attempt == max_retries:
                    raise GeocodingError(f"Geocoding service unavailable for '{query}': {e}")
                time.sleep(0.5 * attempt)

        raise GeocodingError(f"Could not resolve location '{query}' after {max_retries} attempts.")
