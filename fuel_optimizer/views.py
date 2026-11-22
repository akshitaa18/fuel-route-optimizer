import logging
from django.shortcuts import render
from django.conf import settings
from django.db import connection
from rest_framework import status, generics, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

from fuel_optimizer.models import FuelStation, GeocodeCache
from fuel_optimizer.serializers import (
    RouteOptimizeRequestSerializer,
    RouteOptimizeResponseSerializer,
    FuelStationSerializer,
)
from fuel_optimizer.services.geocoding import GeocodingService, GeocodingError
from fuel_optimizer.services.routing import RoutingService, RoutingError
from fuel_optimizer.services.station_filtering import StationFilterService
from fuel_optimizer.services.optimization import FuelRouteOptimizer, OptimizationError

logger = logging.getLogger(__name__)


def map_view(request):
    """Serve the interactive MapLibre GL JS / OpenFreeMap map frontend application."""
    return render(request, 'index.html')


class RouteOptimizeView(APIView):
    """
    Cost-optimal fuel stop calculator for US driving routes.
    Computes route geometry via OSRM, filters nearby fuel stations,
    and calculates the globally minimal fuel purchase sequence using Dynamic Programming.
    """

    @extend_schema(
        request=RouteOptimizeRequestSerializer,
        responses={
            200: RouteOptimizeResponseSerializer,
            400: dict,
            422: dict,
            500: dict,
        },
        description=(
            "Accepts a US origin and destination. Returns optimal driving route geometry, "
            "recommended fuel stops respecting 500-mile vehicle range, fuel consumption, "
            "and total estimated expenditure."
        ),
        examples=[
            OpenApiExample(
                name="New York to Los Angeles Example",
                value={
                    "start": "New York, NY",
                    "finish": "Los Angeles, CA",
                    "max_range": 500,
                    "miles_per_gallon": 10,
                    "corridor_miles": 5
                },
                request_only=True,
            )
        ]
    )
    def post(self, request, *args, **kwargs):
        serializer = RouteOptimizeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    'success': False,
                    'error': 'Invalid request parameters',
                    'details': serializer.errors
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        validated = serializer.validated_data
        start_query = validated['start'].strip()
        finish_query = validated['finish'].strip()
        max_range = validated.get('max_range', getattr(settings, 'DEFAULT_MAX_RANGE_MILES', 500.0))
        mpg = validated.get('miles_per_gallon', getattr(settings, 'DEFAULT_MPG', 10.0))
        tank_capacity = validated.get('tank_capacity', getattr(settings, 'DEFAULT_TANK_CAPACITY_GALLONS', 50.0))
        corridor_miles = validated.get('corridor_miles', getattr(settings, 'DEFAULT_CORRIDOR_MILES', 5.0))

        # 1. Geocode Start & Finish with US Validation
        try:
            start_geo = GeocodingService.geocode(start_query)
        except GeocodingError as e:
            return Response(
                {
                    'success': False,
                    'error': f"Failed to geocode starting location: {e}",
                    'error_type': 'GeocodingError'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            finish_geo = GeocodingService.geocode(finish_query)
        except GeocodingError as e:
            return Response(
                {
                    'success': False,
                    'error': f"Failed to geocode destination: {e}",
                    'error_type': 'GeocodingError'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        start_coords = (start_geo['latitude'], start_geo['longitude'])
        finish_coords = (finish_geo['latitude'], finish_geo['longitude'])

        # 2. Driving Route Calculation via OSRM
        try:
            route_data = RoutingService.get_route(
                start_coords=start_coords,
                finish_coords=finish_coords,
                start_label=start_geo['formatted_address'],
                finish_label=finish_geo['formatted_address']
            )
        except RoutingError as e:
            return Response(
                {
                    'success': False,
                    'error': f"Routing engine error: {e}",
                    'error_type': 'RoutingError'
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        # 3. Filter Eligible Stations within Lateral Corridor
        try:
            eligible_stations = StationFilterService.filter_stations_along_route(
                route_coordinates=route_data['coordinates'],
                corridor_miles=corridor_miles,
                total_route_distance_miles=route_data['distance_miles']
            )
        except Exception as e:
            logger.exception("Error filtering stations along route: %s", e)
            return Response(
                {
                    'success': False,
                    'error': "An internal error occurred while identifying fuel stations along the route.",
                    'error_type': 'SpatialFilterError'
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # 4. Dynamic Programming Cost Optimization
        try:
            optimizer = FuelRouteOptimizer(
                max_range_miles=max_range,
                miles_per_gallon=mpg,
                tank_capacity_gallons=tank_capacity,
                origin_refuel_radius_miles=getattr(settings, 'DEFAULT_ORIGIN_REFUEL_RADIUS_MILES', 25.0)
            )
            result = optimizer.optimize_route(
                start_location=start_geo['formatted_address'],
                start_coords=start_coords,
                finish_location=finish_geo['formatted_address'],
                finish_coords=finish_coords,
                total_distance_miles=route_data['distance_miles'],
                duration_hours=route_data['duration_hours'],
                route_geometry=route_data['geometry'],
                eligible_stations=eligible_stations
            )
            return Response(result, status=status.HTTP_200_OK)

        except OptimizationError as e:
            return Response(
                {
                    'success': False,
                    'error': str(e),
                    'error_type': 'OptimizationError',
                    'route_distance_miles': route_data['distance_miles'],
                    'stations_in_corridor': len(eligible_stations),
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        except Exception as e:
            logger.exception("Unexpected error during route optimization: %s", e)
            return Response(
                {
                    'success': False,
                    'error': "An unexpected internal error occurred while calculating the optimal fuel route.",
                    'error_type': 'InternalError'
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class FuelStationListView(generics.ListAPIView):
    """
    Search and filter fuel stations stored in the database.
    Supports filtering by US state, city, and retail price bounds.
    """
    serializer_class = FuelStationSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ['fuel_price', 'station_name', 'city', 'state']
    ordering = ['fuel_price']

    @extend_schema(
        parameters=[
            OpenApiParameter('state', str, description="Filter by 2-letter US state code (e.g. TX, OH)"),
            OpenApiParameter('city', str, description="Filter by city name (e.g. Dallas)"),
            OpenApiParameter('min_price', float, description="Minimum fuel price ($/gal)"),
            OpenApiParameter('max_price', float, description="Maximum fuel price ($/gal)"),
        ]
    )
    def get_queryset(self):
        queryset = FuelStation.objects.all()
        params = self.request.query_params

        state = params.get('state')
        if state:
            queryset = queryset.filter(state__iexact=state.strip())

        city = params.get('city')
        if city:
            queryset = queryset.filter(city__icontains=city.strip())

        min_price = params.get('min_price')
        if min_price:
            try:
                queryset = queryset.filter(fuel_price__gte=float(min_price))
            except ValueError:
                pass

        max_price = params.get('max_price')
        if max_price:
            try:
                queryset = queryset.filter(fuel_price__lte=float(max_price))
            except ValueError:
                pass

        return queryset


class HealthCheckView(APIView):
    """
    Health check endpoint verifying database connectivity and service availability.
    """

    @extend_schema(responses={200: dict, 503: dict})
    def get(self, request, *args, **kwargs):
        db_connected = False
        db_error = None
        station_count = 0
        geocode_count = 0

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            db_connected = True
            station_count = FuelStation.objects.count()
            geocode_count = GeocodeCache.objects.count()
        except Exception as e:
            logger.exception("Database health check probe failed")
            db_error = str(e)

        overall_status = 'healthy' if db_connected else 'unhealthy'
        http_status = status.HTTP_200_OK if db_connected else status.HTTP_503_SERVICE_UNAVAILABLE

        response_payload = {
            'status': overall_status,
            'application': 'Fuel Route Optimizer API',
            'database': {
                'connected': db_connected,
                'total_stations': station_count,
                'cached_geocodes': geocode_count,
            },
            'services': {
                'osrm_endpoint': getattr(settings, 'OSRM_BASE_URL', 'http://router.project-osrm.org'),
                'nominatim_endpoint': 'https://nominatim.openstreetmap.org',
            },
            'defaults': {
                'max_range_miles': getattr(settings, 'DEFAULT_MAX_RANGE_MILES', 500.0),
                'mpg': getattr(settings, 'DEFAULT_MPG', 10.0),
                'tank_capacity_gallons': getattr(settings, 'DEFAULT_TANK_CAPACITY_GALLONS', 50.0),
                'corridor_miles': getattr(settings, 'DEFAULT_CORRIDOR_MILES', 5.0),
            },
            'version': '1.0.0'
        }
        if db_error:
            response_payload['database']['error'] = db_error

        return Response(response_payload, status=http_status)
