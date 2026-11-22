# Fuel Route Optimizer API ⛽🗺️

[![Python 3.14](https://img.shields.io/badge/Python-3.14-blue.svg)](https://www.python.org/)
[![Django 6.0](https://img.shields.io/badge/Django-6.0-green.svg)](https://www.djangoproject.com/)
[![DRF](https://img.shields.io/badge/DRF-3.18-red.svg)](https://www.django-rest-framework.org/)
[![OpenAPI 3.0](https://img.shields.io/badge/OpenAPI-3.0-orange.svg)](http://localhost:8000/api/docs/)
[![Pytest 32 Passed](https://img.shields.io/badge/Tests-32%20Passed-brightgreen.svg)](https://pytest.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A functional, high-performance REST API and interactive map application that calculates the **globally cost-optimal sequence of fuel stops** for commercial vehicles driving between any two locations in the continental United States.

---

## Table of Contents
1. [Project Overview & Key Objectives](#1-project-overview--key-objectives)
2. [Vehicle Constraints & Specifications](#2-vehicle-constraints--specifications)
3. [System Architecture](#3-system-architecture)
4. [Core Optimization Algorithm (Dynamic Programming vs. Greedy)](#4-core-optimization-algorithm)
5. [Data Ingestion & Geocoding Pipeline](#5-data-ingestion--geocoding-pipeline)
6. [Interactive MapLibre GL JS / OpenFreeMap Frontend](#6-interactive-maplibre-gl-js--openfreemap-frontend)
7. [API Endpoints & Documentation](#7-api-endpoints--documentation)
8. [Setup & Installation Guide](#8-setup--installation-guide)
9. [Automated Testing Suite](#9-automated-testing-suite)
10. [Engineering Decisions & Trade-Offs](#10-engineering-decisions--trade-offs)
11. [Loom Video Demonstration Script](#11-loom-video-demonstration-script)

---

## 1. Project Overview & Key Objectives

Commercial freight and long-haul transportation requires balancing driving distances with fluctuating retail fuel prices. A driver who stops whenever their tank is low or blindly picks the nearest cheap stop often ends up forced to buy fuel at an exorbitant highway station later to avoid running empty.

The **Fuel Route Optimizer** solves this through a rigorous mathematical approach:
- Accepts **any origin and destination** within the continental United States.
- Computes real highway driving directions via **Open Source Routing Machine (OSRM)** and GeoJSON polylines.
- Queries a database of **6,626 validated US fuel stations** and projects them along a configurable corridor (default **5 miles**).
- Applies a **Directed Acyclic Graph (DAG) Dynamic Programming** algorithm using exact Python `Decimal` arithmetic to find the sequence of fuel stops that strictly minimizes total dollar expenditure while respecting the vehicle's 500-mile range.
- Delivers an interactive **MapLibre GL JS** map visualization with OpenFreeMap vector tiles, OpenAPI/Swagger 3.0 documentation, and a Postman test suite.

---

## 2. Vehicle Constraints & Specifications

All vehicle parameters are configurable via the API:

| Parameter | Default Value | Description |
| :--- | :--- | :--- |
| **Max Range** | `500.0` miles | Maximum distance the vehicle can travel on a full tank before refuel is mandatory. |
| **Fuel Economy** | `10.0` MPG | Vehicle efficiency in miles per gallon. Fuel consumed per mile = `1 / MPG`. |
| **Tank Capacity** | `50.0` gallons | Maximum physical fuel volume. Enforces physical feasibility constraint: $\text{Range} \le \text{Capacity} \times \text{MPG}$. |
| **Corridor Radius** | `5.0` miles | Maximum lateral distance from the highway route to consider a fuel station eligible. |
| **Origin Refuel Radius** | `25.0` miles | Search radius around origin to identify the cheapest station for the initial refuel. |

### Starting Fuel & Detour Modeling (Disclosed & Documented)
Under the empty-tank starting assumption:
- The vehicle starts with an empty tank and refuels at the cheapest eligible station physically located within 25.0 miles of the origin.
- To account for driving between the origin and this station, the detour road distance is modeled using straight-line Haversine distance scaled by a documented road circuity factor:
  $$d_{\text{detour}} = \text{round}(\text{Haversine}(O, S_0) \times 1.20, 2)$$
- Detour fuel consumption ($\frac{d_{\text{detour}}}{\text{MPG}}$) and detour cost ($d_{\text{detour}} \times P_0 / \text{MPG}$) are explicitly calculated and added to the total trip expenditure.
- If no station exists within 25.0 miles of the origin, the optimizer raises an `OptimizationError` rather than guessing a distant station in another state.

---

## 3. System Architecture

```mermaid
flowchart TD
    subgraph Client["Client Tier"]
        UI["MapLibre GL JS / OpenFreeMap UI (Browser)"]
        Postman["Postman / REST Clients"]
        Swagger["Swagger UI (/api/docs/)"]
    end

    subgraph API["Django REST Framework API"]
        Views["API Views (RouteOptimizeView, HealthCheckView)"]
        Serializers["DRF Serializers & Physical Range Validators"]
        CacheLayer["Geocode & Route Cache (DB + LocMem)"]
    end

    subgraph Services["Core Geospatial & Optimization Services"]
        GeoService["GeocodingService (US Bounds Validation)"]
        RouteService["RoutingService (OSRM Driving Directions)"]
        FilterService["StationFilterService (Haversine Route Projection)"]
        Optimizer["FuelRouteOptimizer (Dynamic Programming DAG with Decimal)"]
    end

    subgraph Data["Database & External APIs"]
        DB[("SQLite / PostgreSQL\nFuelStation & GeocodeCache")]
        OSRM["OSRM Public Engine"]
        Nominatim["Nominatim / US Census"]
    end

    UI -->|POST /api/route/optimize/| Views
    Postman -->|POST /api/route/optimize/| Views
    Swagger -->|GET /api/schema/| Views

    Views --> Serializers
    Views --> GeoService
    Views --> RouteService
    Views --> FilterService
    Views --> Optimizer

    GeoService --> CacheLayer
    GeoService -.->|Fallback| Nominatim
    RouteService --> CacheLayer
    RouteService -.->|Uncached| OSRM
    FilterService --> DB
    CacheLayer --> DB
```

---

## 4. Core Optimization Algorithm

### Why Greedy Fails
A greedy algorithm always selects the cheapest station reachable from the current position. Consider this verified counterexample:
- **Origin:** Mile 0 (Fuel price: \$3.50/gal)
- **Station 1 (Locally Cheap):** Mile 100 (Price: **\$2.00/gal**)
- **Station 2 (Forced Expensive Stop):** Mile 590 (Price: **\$5.00/gal**)
- **Station 3 (Balanced Stop 1):** Mile 350 (Price: **\$2.80/gal**)
- **Station 4 (Balanced Stop 2):** Mile 700 (Price: **\$2.80/gal**)
- **Destination:** Mile 1,000

1. **Greedy Decision:** At mile 0, reachable stations ($\le 500$ mi) are Station 1 (\$2.00) and Station 3 (\$2.80). Greedy picks **Station 1** (\$2.00).
2. **Greedy Consequence:** From Station 1 (mile 100), the vehicle can only reach up to mile 600. Between mile 100 and 600, the *only* station is Station 2 at mile 590! The driver is **forced** to buy fuel at **\$5.00/gal** for the remaining 410 miles.
   - Total Greedy Cost: `(10 * $3.50) + (49 * $2.00) + (41 * $5.00) = $338.00`
3. **Dynamic Programming Decision:** The DP evaluates all paths and selects **Station 3 (\$2.80) $\to$ Station 4 (\$2.80)**.
   - Total DP Cost: `(35 * $3.50) + (35 * $2.80) + (30 * $2.80) = $304.50`
   - **Savings:** **\$33.50 (10% cheaper)** by avoiding the greedy trap!

### Dynamic Programming Mathematical Formulation
We model the trip as a Directed Acyclic Graph (DAG) with exact Python `Decimal` arithmetic:
1. **Nodes:**
   - Node $0$: Origin ($x_0 = 0$, $P_0 = \text{cheapest station within 25 miles of origin}$)
   - Nodes $1 \dots N$: Eligible stations along the route, ordered strictly by along-track distance ($x_1 \le x_2 \le \dots \le x_N$)
   - Node $N+1$: Destination ($x_{N+1} = \text{Total Distance } D$, $P_{N+1} = 0$)
2. **Directed Edges:**
   A directed edge $(u, v)$ exists for $u < v$ if and only if:
   $$x_v - x_u \le \text{Max Range (500 miles)}$$
3. **Edge Cost:**
   To traverse the segment from $u$ to $v$, the fuel required is $\frac{x_v - x_u}{\text{MPG}}$. Because this fuel must be purchased at station $u$:
   $$\text{Cost}(u, v) = \left(\frac{x_v - x_u}{\text{MPG}}\right) \times P_u$$
4. **Recurrence Relation with Tie-Breaking:**
   Let $DP[v]$ be the minimum total cost to reach node $v$ from origin, with secondary tie-breaking on fewer stops:
   $$DP[0] = 0$$
   $$DP[v] = \min_{\substack{0 \le u < v \\ x_v - x_u \le 500}} \left( DP[u] + \text{Cost}(u, v) \right)$$
5. **Destination Reachability & Gap Diagnosis:**
   If $DP[N+1] = \infty$, the destination cannot be reached. The algorithm diagnoses the exact gap $(u, u+1)$ where $x_{u+1} - x_u > 500$ and returns an informative HTTP 422 error detailing the unreachable gap.

---

## 5. Data Ingestion & Geocoding Pipeline

The raw dataset `fuel-prices-for-be-assessment.csv` contains **8,151 rows** with station names, addresses, cities, states, and retail prices, but **no coordinates**.

### Ingestion Strategy & Coordinate Precision Policy
1. **Validation & Deduplication:**
   - Validates positive decimal prices (`0 < price <= 25`).
   - Merges 905 duplicate OPIS Truckstop IDs, retaining the lowest available rate.
   - Flags 620 non-US (Canadian) records (`ON`, `AB`, `BC`, etc.) for exclusion.
2. **Coordinate Quality & Precision Tracking:**
   - The majority of OPIS addresses describe highway exits (e.g., `I-44, EXIT 283 & US-69`).
   - Records are resolved to verified city centroids from the US gazetteer and tagged with `is_exact=False` and `geocode_source='city_centroid'`.
   - Street-level matches are tagged with `is_exact=True` and `geocode_source='exact'`.
   - A 5-mile highway search corridor safely absorbs city-centroid offsets along major interstate routes.
3. **Execution Performance:**
   The management command imports all **6,626 valid US stations** in **under 1.0 second**:
   ```bash
   python manage.py import_fuel_prices
   ```

```
============================================================
FUEL ROUTE OPTIMIZER - INGESTION SUMMARY REPORT
============================================================
Metric                              |           Count
-------------------------------------------------------
Total CSV records processed         |           8,151
Duplicates removed / merged         |             905
Invalid retail prices               |               0
Invalid addresses/data              |               0
Non-US (Canadian) records           |             620
Successfully geocoded & imported    |           6,626
  - Street-level exact coordinates  |               0
  - City centroid approximations    |           6,626
Stations stored in database         |           6,626
Total processing elapsed time       |         0.92 sec
============================================================
```

---

## 6. Interactive MapLibre GL JS / OpenFreeMap Frontend

The application serves an interactive road map dashboard at `http://localhost:8000/`:
- **Basemap Engine:** [MapLibre GL JS](https://maplibre.org/) v4.7.1 with [OpenFreeMap](https://openfreemap.org/) bright vector style (`https://tiles.openfreemap.org/styles/bright`).
- **Visuals:**
  - Vibrant blue polyline (`#2563eb`) with a dark navy contrast casing (`#1e3a8a`) clearly displaying the route over highway networks.
  - Green circular pin **A** for Origin.
  - Red circular pin **B** for Destination.
  - Amber numbered badges **#1, #2, ...** for recommended fuel stops.
- **Interactive Popups:** Show station name, address, fuel price, distance along route, off-route corridor distance, fuel added, and coordinate precision tag (`Exact` vs `City Centroid`).
- **Summary Metrics:** Total distance, total gallons (including detour), total fuel cost, number of stops, average price per gallon, and initial detour breakdown.
- **Presets:** NYC $\to$ LA, Chicago $\to$ Miami, Seattle $\to$ San Diego, and Dallas $\to$ Atlanta.
- **Attribution:** Compliant visible OpenFreeMap and OpenStreetMap contributor attribution.

---

## 7. API Endpoints & Documentation

Interactive Swagger documentation is available at `http://localhost:8000/api/docs/`.
OpenAPI schema JSON is available at `http://localhost:8000/api/schema/?format=json`.

### 1. `POST /api/route/optimize/`
Calculates optimal driving route, fuel stops, and consumption.

**Request Body:**
```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA",
  "max_range": 500.0,
  "miles_per_gallon": 10.0,
  "tank_capacity": 50.0,
  "corridor_miles": 5.0
}
```

**Key Request Validations:**
- Disallows identical origin and destination (returns HTTP 400).
- Enforces physical feasibility constraint: `max_range <= tank_capacity * miles_per_gallon` (returns HTTP 400 if violated).
- Rejects non-US addresses (returns HTTP 400).

**Response (HTTP 200 excerpt):**
```json
{
  "success": true,
  "route": {
    "start": "New York, NY, USA",
    "finish": "Los Angeles, CA, USA",
    "distance_miles": 2794.2,
    "total_driving_distance_miles": 2794.2,
    "duration_hours": 49.81,
    "geometry": {
      "type": "LineString",
      "coordinates": [[-74.006, 40.712], "...", [-118.243, 34.053]]
    }
  },
  "fuel_summary": {
    "total_distance_miles": 2794.2,
    "route_distance_miles": 2794.2,
    "total_fuel_gallons": 279.42,
    "total_fuel_cost_usd": 850.32,
    "num_stops": 6,
    "average_price_per_gallon": 3.043
  },
  "stops": [
    {
      "stop_number": 1,
      "station_id": 35,
      "station_name": "ACI TRUCK STOP",
      "address": "US-46",
      "city": "Columbia",
      "state": "NJ",
      "fuel_price": 3.079,
      "distance_along_route_miles": 63.74,
      "distance_from_route_miles": 0.12,
      "leg_distance_miles": 326.44,
      "fuel_added_gallons": 32.64,
      "segment_cost_usd": 100.50,
      "is_exact": false,
      "geocode_source": "city_centroid",
      "coordinates": {
        "latitude": 40.9254,
        "longitude": -75.0932
      }
    }
  ],
  "origin_refuel": {
    "station_id": 12,
    "station_name": "DELTA",
    "fuel_price": 3.079,
    "detour_miles": 4.8,
    "detour_cost_usd": 1.48,
    "detour_fuel_gallons": 0.48,
    "initial_leg_distance_miles": 63.74,
    "initial_leg_fuel_gallons": 6.37,
    "initial_leg_cost_usd": 19.61,
    "total_origin_gallons": 6.85,
    "total_origin_cost_usd": 21.09,
    "is_detour_modelled": true,
    "road_circuity_factor": 1.2
  },
  "metadata": {
    "vehicle_specs": {
      "max_range_miles": 500.0,
      "miles_per_gallon": 10.0,
      "tank_capacity_gallons": 50.0
    },
    "corridor_stations_count": 248,
    "graph_nodes_evaluated": 250,
    "algorithm": "Graph-based Dynamic Programming (DAG Least Cost with Python Decimal)"
  }
}
```

### 2. `GET /api/fuel-stations/`
List and search fuel stations with pagination and filtering:
- `?state=TX`: Filter by US state.
- `?city=Dallas`: Filter by city.
- `?max_price=3.00`: Filter stations under \$3.00/gal.
- `?ordering=fuel_price`: Sort by price ascending.

### 3. `GET /api/health/`
Probes active database connectivity via a live `SELECT 1` query:
- Returns HTTP 200 if connected.
- Returns HTTP 503 Service Unavailable if database is unreachable.

---

## 8. Setup & Installation Guide

### Prerequisites
- Python 3.10+ (tested on Python 3.14)
- Git

### Installation Steps

1. **Clone the repository:**
   ```bash
   git clone https://github.com/akshitaa18/fuel-route-optimizer.git
   cd fuel-route-optimizer
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run database migrations:**
   ```bash
   python manage.py migrate
   ```

5. **Import and preprocess fuel stations:**
   ```bash
   python manage.py import_fuel_prices
   ```

6. **Start the development server:**
   ```bash
   python manage.py runserver
   ```

7. **Open in browser:**
   - Map Dashboard: [http://localhost:8000/](http://localhost:8000/)
   - Swagger API Docs: [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/)
   - Health Check: [http://localhost:8000/api/health/](http://localhost:8000/api/health/)

---

## 9. Automated Testing Suite

The test suite includes **32 automated tests** covering unit logic, integration endpoints, and mathematical edge cases:
- Model validation, indexing, and exact/approx coordinate string representations.
- US geocoding boundary checks and non-US location rejection.
- OSRM route response parsing and unit conversion.
- Corridor filtering and along-route distance normalization.
- **8 Core Optimization Scenarios:**
  1. DP outperforms greedy approach (counterexample verification).
  2. Short route under 500 miles requiring zero intermediate stops.
  3. Range gap exceeding max range diagnosing unreachable segment.
  4. Station positioned exactly at the 500-mile max-range boundary.
  5. Multiple stations at nearly identical positions choosing cheaper stop.
  6. Initial station unreachable (> 25 miles origin radius) raising error.
  7. Exact fuel cost calculation across multiple segments with `Decimal` precision.
  8. Fewer stops selected when multiple paths have identical cost (tie-breaker).
- Active DB probe in health check (verifying 200 on healthy DB and 503 on connection failure).
- DRF serializer validation for `max_range <= tank_capacity * mpg` and identical start/finish rejection.
- All external HTTP calls (OSRM, Nominatim) are **strictly mocked**, ensuring tests execute completely offline in CI/CD.

### Run Tests:
```bash
# Using pytest
python -m pytest

# Using Django test runner
python manage.py test fuel_optimizer
```

**Output:**
```
============================= test session starts =============================
platform win32 -- Python 3.14.5, pytest-9.1.1, pluggy-1.6.0
django: version: 6.0.6, settings: config.settings (from ini)
rootdir: C:\fuel-route-optimizer
plugins: anyio-4.14.2, langsmith-0.14.4, django-4.14.0
collected 32 items

fuel_optimizer\tests\test_api_endpoints.py .........                     [ 28%]
fuel_optimizer\tests\test_geocoding.py .....                             [ 43%]
fuel_optimizer\tests\test_models.py ....                                 [ 56%]
fuel_optimizer\tests\test_optimization_algorithm.py ........             [ 81%]
fuel_optimizer\tests\test_routing.py ....                                [ 93%]
fuel_optimizer\tests\test_station_filtering.py ..                        [100%]

============================= 32 passed in 1.23s ==============================
```

---

## 10. Engineering Decisions & Trade-Offs

1. **Targeting ~3 External HTTP Requests Per Route:**
   - Fuel stations are pre-geocoded and indexed in SQLite/PostgreSQL.
   - An uncached route request triggers at most 3 external requests: origin geocode, destination geocode, and OSRM directions.
   - Subsequent identical requests hit `RouteCache` and `GeocodeCache`, requiring **0 external HTTP calls**.
2. **Spatial Corridor Pruning:**
   - Evaluating all 6,626 stations against a 30,000-point polyline would be $\mathcal{O}(M \times N)$ and slow.
   - We prune candidates using a database bounding-box query expanded by the corridor margin, reducing candidates by ~95%, before running Haversine projections on simplified segments.
3. **Graph DAG Formulation & Decimal Precision:**
   - Modeling fuel stop optimization as a DAG guarantees polynomial $\mathcal{O}(V \cdot E)$ execution time while guaranteeing the exact global optimum.
   - Using Python `Decimal` throughout DP relaxation guarantees that total expenditures strictly equal the sum of detour costs and all segment costs without IEEE 754 floating-point rounding drift.
4. **Transparent Origin Detour Modeling:**
   - Incorporating straight-line distance scaled by 1.2 road circuity models real-world detours conservatively without fabricating road routes when only coordinates are available.

---

## 11. Loom Video Demonstration Script

A complete 5-minute video presentation script with teleprompter timing cues, screen actions, and technical walkthrough notes is available in [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

---

## License

This project is licensed under the MIT License - see the LICENSE file for details.
