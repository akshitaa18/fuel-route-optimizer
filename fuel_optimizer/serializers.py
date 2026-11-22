from rest_framework import serializers
from fuel_optimizer.models import FuelStation


class RouteOptimizeRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        required=True,
        trim_whitespace=True,
        help_text="Starting location name or address within the USA (e.g. 'New York, NY')"
    )
    finish = serializers.CharField(
        required=True,
        trim_whitespace=True,
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
    tank_capacity = serializers.FloatField(
        required=False,
        default=50.0,
        min_value=5.0,
        max_value=500.0,
        help_text="Vehicle fuel tank capacity in gallons (default: 50)"
    )
    corridor_miles = serializers.FloatField(
        required=False,
        default=5.0,
        min_value=0.5,
        max_value=50.0,
        help_text="Search corridor radius on either side of the driving route in miles (default: 5)"
    )

    def validate_start(self, value):
        s = value.strip()
        if not s:
            raise serializers.ValidationError("Starting location cannot be blank or whitespace.")
        return s

    def validate_finish(self, value):
        f = value.strip()
        if not f:
            raise serializers.ValidationError("Destination location cannot be blank or whitespace.")
        return f

    def validate(self, attrs):
        start = attrs.get('start', '').strip()
        finish = attrs.get('finish', '').strip()
        if start and finish and start.lower() == finish.lower():
            raise serializers.ValidationError({
                "finish": "Destination cannot be identical to the starting location."
            })

        max_range = attrs.get('max_range', 500.0)
        mpg = attrs.get('miles_per_gallon', 10.0)
        tank_capacity = attrs.get('tank_capacity', 50.0)

        max_physical_range = round(tank_capacity * mpg, 2)
        if max_range > max_physical_range:
            raise serializers.ValidationError({
                "max_range": (
                    f"Configured maximum range ({max_range:.1f} mi) cannot exceed physical vehicle range "
                    f"({tank_capacity:.1f} gal × {mpg:.1f} mpg = {max_physical_range:.1f} mi)."
                )
            })

        return attrs


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
    is_exact = serializers.BooleanField(required=False, default=True)
    geocode_source = serializers.CharField(required=False, default='exact')


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
            'latitude', 'longitude', 'is_exact', 'geocode_source',
            'created_at', 'updated_at'
        ]
