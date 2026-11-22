# 5-Minute Video Demonstration Script: Fuel Route Optimizer

**Presenter:** Backend & Full-Stack Developer  
**Platform:** Loom / Screen Recording  
**Duration:** ~5 Minutes  
**Target Audience:** Technical Evaluators, Engineering Leadership  

---

## Overview & Timeline Summary

| Time Marker | Segment | Screen Content | Key Narrative |
| :--- | :--- | :--- | :--- |
| **0:00 – 0:45** | Introduction & Problem Scope | Architecture Diagram & Tech Stack | Problem statement, 500-mile vehicle range constraint, tech stack |
| **0:45 – 1:30** | Interactive Map Demonstration | Browser at `http://localhost:8000` | Routing dashboard, MapLibre/OpenFreeMap visualization, stop popups |
| **1:30 – 2:45** | Core Optimization: DP vs Greedy | Code Walkthrough (`optimization.py`) | Mathematical model, DAG relaxation with Decimal precision, exact tie-breaking |
| **2:45 – 3:45** | Geospatial Data & Ingestion | Management Pipeline & Services | Preprocessing 8k stations, coordinate precision policy, corridor filtering |
| **3:45 – 4:30** | REST API & Documentation | Swagger UI (`/api/docs/`) & Health Check | OpenAPI 3.0 schema, input validation, active DB health probe |
| **4:30 – 5:00** | Test Suite & Conclusion | Terminal running Pytest & Django test runner | 33 passing automated tests (100% offline), project summary |

---

## Detailed Script & Teleprompter

### Segment 1: Introduction & Architecture (0:00 – 0:45)
**Screen Action:**
- Show repository overview and high-level architecture diagram.
- Terminal showing Django project structure.

**Spoken Words:**
> "Hello! Today I am presenting the **Fuel Route Optimizer**, a Django-based REST API and interactive map dashboard designed to determine a cost-effective sequence of fuel stops for commercial vehicles driving between any two locations in the continental United States.
>
> The vehicle operates under standard commercial specifications: a maximum driving range of 500 miles on a 50-gallon tank at 10 miles per gallon.
> 
> The application is built using **Python 3.14**, **Django 6**, and **Django REST Framework**, with **Pandas** for fuel price dataset ingestion, **OSRM** for road driving directions, and **MapLibre GL JS** with **OpenFreeMap** for client-side vector map rendering. Let's look at the working dashboard."

---

### Segment 2: Interactive Map Demonstration (0:45 – 1:30)
**Screen Action:**
- Switch browser to `http://localhost:8000`.
- Click preset: **NYC → LA**.
- Point cursor to Start (Green Pin), Finish (Red Pin), and Amber Fuel Stop Pins.
- Hover over an intermediate stop popup showing Station Name, Retail Price, Distance Along Route, Segment Cost, and Coordinate Precision tag.

**Spoken Words:**
> "Here is the interactive dashboard. Let's run a route from **New York City to Los Angeles**.
>
> When I click **Optimize**, the system geocodes origin and destination, retrieves the driving route polyline from OSRM, filters fuel stations within a 5-mile corridor, and computes the dynamic programming sequence.
>
> On the map:
> - The driving route is highlighted prominently with high contrast against the OpenFreeMap light road basemap.
> - Pre-trip refuelling assumption: We select the cheapest station within 25 miles straight-line of the origin to establish the starting fuel price at route mile zero.
> - The optimizer selects intermediate fuel stops along the interstate corridor such that no driving segment exceeds the 500-mile vehicle range.
> - Each stop displays its retail price, along-route distance, and a precision tag indicating whether the coordinates are exact or city-centroid approximations."

---

### Segment 3: Dynamic Programming vs Greedy (1:30 – 2:45)
**Screen Action:**
- Switch to IDE. Open `fuel_optimizer/services/optimization.py`.
- Highlight the DAG edge creation, exact Decimal comparisons, and relaxation loop.

**Spoken Words:**
> "Let’s look at the core optimization algorithm in `optimization.py`.
>
> Why is a greedy approach insufficient? A greedy algorithm chooses the cheapest station reachable from the current position. That choice can lead the vehicle into a fuel desert where it is forced to buy fuel at an exorbitant highway station to avoid running empty.
>
> We model the corridor as a **Directed Acyclic Graph (DAG)**:
> 1. Nodes represent the Origin, candidate stations sorted by along-route mileage, and the Destination.
> 2. A directed edge $(u, v)$ exists if and only if the segment distance is $\le 500$ miles.
> 3. Segment fuel cost is calculated as $(\Delta x / \text{MPG}) \times P_u$.
> 4. We use exact Python `Decimal` arithmetic for all comparisons, removing arbitrary floating-point tolerances, and apply an exact tie-breaker preferring paths with fewer stops when costs are equal.
> 5. The displayed initial leg cost and intermediate segment costs strictly reconcile with the reported total fuel cost, preventing component-rounding discrepancies."

---

### Segment 4: Data Pipeline & Spatial Filtering (2:45 – 3:45)
**Screen Action:**
- Open `fuel_optimizer/services/station_filtering.py`.
- Open terminal and show `python manage.py import_fuel_prices` output.

**Spoken Words:**
> "Now let's examine geospatial data handling. The supplied CSV contains **8,151 station records** without latitude and longitude.
>
> In `import_fuel_prices.py`, our preprocessing pipeline:
> - Deduplicates records by OPIS ID, validates prices, and separates international Canadian stations.
> - Resolves coordinates against a local US gazetteer for all 6,626 valid US stations.
> - Tracks coordinate precision: because most OPIS records describe highway exits (e.g., 'I-44 & US-69') rather than street addresses, they resolve to verified city centroids tagged as `is_exact=False`.
>
> During routing, candidate stations are filtered using bounding-box queries and perpendicular cross-track Haversine projection, normalized against OSRM's total driving distance."

---

### Segment 5: REST API & OpenAPI Documentation (3:45 – 4:30)
**Screen Action:**
- Open browser to `http://localhost:8000/api/docs/` (Swagger UI).
- Expand `POST /api/route/optimize/` and execute a request.
- Show `GET /api/health/`.

**Spoken Words:**
> "The API is documented via **drf-spectacular** at `/api/docs/`.
>
> Key validations include:
> - Disallowing blank or identical origin/destination pairs (HTTP 400).
> - Validating physical vehicle feasibility: configured maximum range cannot exceed tank capacity multiplied by MPG (HTTP 400).
> - Diagnosing unreachable gaps greater than 500 miles (HTTP 422).
> - Safe error handling: internal exception details are logged on the server rather than exposed to clients (HTTP 500).
> - The health check endpoint at `/api/health/` actively probes the database with a live query, returning HTTP 200 when healthy or HTTP 503 if the database fails."

---

### Segment 6: Test Suite & Conclusion (4:30 – 5:00)
**Screen Action:**
- Switch to terminal. Run `python -m pytest`.
- Show 33 passed tests.
- Run `python manage.py test fuel_optimizer`.

**Spoken Words:**
> "Finally, let's verify test execution. Running `pytest`:
> **33 automated tests** execute in under 2 seconds.
> 
> All external HTTP calls to OSRM and geocoders are mocked, allowing tests to run completely offline. The suite verifies model integrity, geographic bounding-box accuracy, DP vs. greedy optimality, boundary conditions, and full monetary reconciliation.
>
> Thank you for your time, and I look forward to discussing the implementation!"
