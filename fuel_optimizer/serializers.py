from rest_framework import serializers
from fuel_optimizer.models import FuelStation


class RouteOptimizeRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        required=True,
        help_text="Starting location name or address within the USA (e.g. 'New York, NY')"
    )
    finish = serializers.CharField(
        required=True,
        help_text="Destination location name or address within the USA (e.g. 'Los Angeles, CA')"
    )
    max_range = serializers.FloatField(
        required=False,
        default=500.0,
        min_value=50.0,
        max_value=2000.0,
        help_text="Maximum driving range in miles before refuel is mandatory (default: 500)"
    )
    miles_per_gallon = serializers.FloatField(
        required=False,
        default=10.0,
        min_value=1.0,
        max_value=100.0,
        help_text="Vehicle fuel efficiency in miles per gallon (default: 10)"
    )
    corridor_miles = serializers.FloatField(
        required=False,
        default=5.0,
        min_value=0.5,
        max_value=50.0,
        help_text="Search corridor radius on either side of the driving route in miles (default: 5)"
    )


class FuelStopSerializer(serializers.Serializer):
    stop_number = serializers.IntegerField()
    station_id = serializers.IntegerField()
    station_name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    fuel_price = serializers.FloatField()
    distance_along_route_miles = serializers.FloatField()
    distance_from_route_miles = serializers.FloatField()
    leg_distance_miles = serializers.FloatField()
    fuel_added_gallons = serializers.FloatField()
    segment_cost_usd = serializers.FloatField()
    coordinates = serializers.DictField()


class FuelSummarySerializer(serializers.Serializer):
    total_distance_miles = serializers.FloatField()
    total_fuel_gallons = serializers.FloatField()
    total_fuel_cost_usd = serializers.FloatField()
    num_stops = serializers.IntegerField()
    average_price_per_gallon = serializers.FloatField()


class RouteDetailsSerializer(serializers.Serializer):
    start = serializers.CharField()
    finish = serializers.CharField()
    distance_miles = serializers.FloatField()
    duration_hours = serializers.FloatField()
    geometry = serializers.DictField()


class RouteOptimizeResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True)
    route = RouteDetailsSerializer()
    fuel_summary = FuelSummarySerializer()
    stops = serializers.ListField(child=FuelStopSerializer())
    origin_refuel = serializers.DictField()
    metadata = serializers.DictField()


class FuelStationSerializer(serializers.ModelSerializer):
    class Meta:
        model = FuelStation
        fields = [
            'id', 'station_id', 'station_name', 'address',
            'city', 'state', 'rack_id', 'fuel_price',
            'latitude', 'longitude', 'created_at', 'updated_at'
        ]
