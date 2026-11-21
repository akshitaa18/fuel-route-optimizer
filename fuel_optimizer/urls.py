from django.urls import path
from fuel_optimizer.views import (
    RouteOptimizeView,
    FuelStationListView,
    HealthCheckView,
)

app_name = 'fuel_optimizer'

urlpatterns = [
    path('route/optimize/', RouteOptimizeView.as_view(), name='route-optimize'),
    path('fuel-stations/', FuelStationListView.as_view(), name='fuel-stations'),
    path('health/', HealthCheckView.as_view(), name='health'),
]
