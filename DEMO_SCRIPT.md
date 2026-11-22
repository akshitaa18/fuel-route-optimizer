# 5-Minute Video Demonstration Script: Fuel Route Optimizer

**Presenter:** Senior Backend Engineer & Full-Stack Developer  
**Platform:** Loom / Screen Recording  
**Duration:** ~5 Minutes  
**Target Audience:** Technical Hiring Committee, Engineering Leadership, Assessment Evaluators  

---

## Overview & Timeline Summary

| Time Marker | Segment | Screen Content | Key Narrative |
| :--- | :--- | :--- | :--- |
| **0:00 – 0:45** | Introduction & Problem Scope | Architecture Diagram & Tech Stack | Problem statement, 500-mile range constraint, tech stack |
| **0:45 – 1:30** | Interactive Map MVP Demonstration | Browser at `http://localhost:8000` | Transcontinental routing (NYC → LA), MapLibre/OpenFreeMap visualization, detour details |
| **1:30 – 2:45** | Core Optimization: DP vs Greedy | Code Walkthrough (`optimization.py`) | Mathematical model, DAG relaxation with Decimal precision, tie-breaking |
| **2:45 – 3:45** | Performance & Geospatial Design | Management Pipeline & Services | Preprocessing 8k stations, coordinate precision policy, corridor filtering |
| **3:45 – 4:30** | API Endpoints & Swagger Docs | Postman & Swagger UI (`/api/docs/`) | OpenAPI 3.0 schema, physical range validation, active DB health probe |
| **4:30 – 5:00** | Test Suite & Conclusion | Terminal running Pytest & GitHub repo | 32 passing automated tests (100% offline), technical reliability |

---

## Detailed Script & Teleprompter

### Segment 1: Introduction & Architecture (0:00 – 0:45)
**Screen Action:**
- Show repository overview and high-level architecture diagram.
- Terminal showing Django project structure.

**Spoken Words:**
> "Hello everyone! Today I am presenting the **Fuel Route Optimizer**, a functional backend API and interactive mapping service built to solve a critical logistical challenge: finding the globally cost-optimal sequence of fuel stops for commercial vehicles driving between any two locations in the continental United States.
>
> Our vehicle operates under strict physical constraints: a maximum driving range of 500 miles on a full 50-gallon tank, achieving 10 miles per gallon.
> 
> The technology stack is powered by **Python 3.14**, **Django 6**, and **Django REST Framework**, with **Pandas** for fuel data preprocessing, **OSRM** for highway driving directions, and **MapLibre GL JS** with **OpenFreeMap** for modern vector map rendering. Let's dive right into the working application."

---

### Segment 2: Interactive Map Demonstration (0:45 – 1:30)
**Screen Action:**
- Switch browser to `http://localhost:8000`.
- Click preset: **NYC → LA**.
- Point cursor to Start (Green Pin), Finish (Red Pin), and Amber Fuel Stop Pins.
- Hover over an intermediate stop popup showing Station Name, Retail Price, Distance Along Route, Segment Cost, and Coordinate Precision.

**Spoken Words:**
> "Here is our live dashboard. Let's run a full transcontinental route from **New York City to Los Angeles**.
>
> When I click **Optimize**, the system geocodes both points, queries the OSRM highway routing engine, projects fuel stations within a 5-mile corridor, and solves the dynamic programming graph in under a second.
>
> As you can see on the map:
> - Total driving distance is **2,794 miles**, requiring **279.4 gallons of fuel**.
> - The vehicle starts with an empty tank: we locate the cheapest reachable station within 25 miles of origin and explicitly calculate the road detour using a 1.2 circuity factor, ensuring the initial refuel cost is fully accounted for.
> - Because 2,794 miles exceeds our 500-mile maximum range, the optimizer has selected **6 cost-optimal refueling stops** along the interstate highway corridor.
> - Notice how each stop is strategically placed: every leg is under 500 miles, and the stations chosen offer the lowest fuel rates along the journey, achieving an average price of **$3.04 per gallon** and saving tens of dollars over a naive greedy choice."

---

### Segment 3: Deep Dive into Dynamic Programming vs Greedy (1:30 – 2:45)
**Screen Action:**
- Switch to VS Code / IDE. Open `fuel_optimizer/services/optimization.py`.
- Highlight the DAG edge creation, Python `Decimal` arithmetic, and dynamic programming relaxation loop.

**Spoken Words:**
> "Let’s look at the heart of this system: the **Core Cost Optimization Algorithm**.
>
> Why is a greedy algorithm insufficient? A greedy algorithm chooses the cheapest station currently reachable. But that greedy choice often leads to a 'dead end' where the vehicle is forced to refuel at an exorbitant highway station 490 miles later just to avoid running out of gas.
>
> To guarantee the **global minimum expenditure**, we model the problem as a **Directed Acyclic Graph (DAG)**:
> 1. Nodes represent the Origin, eligible corridor stations, and Destination, sorted topologically by along-route mileage.
> 2. A directed edge $(u, v)$ exists if and only if the distance along the route between station $u$ and station $v$ is $\le 500$ miles.
> 3. The edge weight is the cost of fuel purchased at station $u$ to traverse the segment: $\Delta x / 10 \times \text{Price}(u)$.
> 4. We compute all transitions using Python's exact `Decimal` type to prevent floating-point rounding drift, with secondary tie-breaking favoring paths with fewer stops when costs are equal.
> 5. We perform dynamic programming relaxation over topological order in $\mathcal{O}(V \cdot E)$ time.
>
> If a corridor has an unbridgeable gap greater than 500 miles, the algorithm immediately catches this and returns an informative HTTP 422 error diagnosing the exact gap location."

---

### Segment 4: Data Pipeline & Spatial Filtering (2:45 – 3:45)
**Screen Action:**
- Open `fuel_optimizer/services/station_filtering.py`.
- Open terminal and show `python manage.py import_fuel_prices` output.

**Spoken Words:**
> "Now let's examine geospatial data quality. The OPIS dataset contains **8,151 station records** without latitude and longitude.
>
> Geocoding 8,000 addresses sequentially over public APIs would take hours and trigger rate limits. We built an offline preprocessed gazetteer and batch ingestion pipeline in `import_fuel_prices.py`.
>
> In just **0.9 seconds**, our management command:
> - Deduplicates records, validates prices, and separates international Canadian stations.
> - Resolves coordinates for all 6,626 US stations, tagging each with an explicit coordinate precision flag (`is_exact` and `geocode_source`).
> - Stores them in SQLite with spatial indexes on `(latitude, longitude)`, `state`, and `fuel_price`.
>
> Furthermore, during routing, we do not evaluate all 6,626 stations. We apply a bounding-box database query, then compute cross-track Haversine projection normalized against OSRM's total driving distance to strictly admit stations within our configurable 5-mile corridor."

---

### Segment 5: REST API & OpenAPI Documentation (3:45 – 4:30)
**Screen Action:**
- Open browser to `http://localhost:8000/api/docs/` (Swagger UI).
- Expand `POST /api/route/optimize/` and execute a request.
- Show `GET /api/health/`.

**Spoken Words:**
> "The API is fully documented with **drf-spectacular** and OpenAPI 3.0 at `/api/docs/`.
>
> The primary endpoint is `POST /api/route/optimize/`. It accepts configurable parameters:
> - `max_range`: defaults to 500 miles.
> - `miles_per_gallon`: defaults to 10 MPG.
> - `tank_capacity`: defaults to 50 gallons.
> - `corridor_miles`: defaults to 5 miles.
>
> It returns the GeoJSON LineString geometry, summary metrics, detour breakdown, and stop-by-stop itinerary.
>
> Input validation is strict:
> - Identical start and finish locations are rejected with HTTP 400.
> - Physical vehicle constraints are validated: configured max range cannot exceed tank capacity multiplied by MPG.
> - Non-US locations are rejected with HTTP 400.
> - And `/api/health/` actively executes a live database probe, returning HTTP 200 when healthy or HTTP 503 if the database is unreachable."

---

### Segment 6: Test Suite & Conclusion (4:30 – 5:00)
**Screen Action:**
- Switch to terminal. Run `python -m pytest`.
- Show 32 passed tests in ~1 second.
- Show Git log and GitHub repository.

**Spoken Words:**
> "Finally, let's verify reliability. When I run `pytest`:
> **32 automated unit and integration tests** execute in just 1 second.
> 
> All external HTTP requests to OSRM and geocoders are strictly mocked, allowing the test suite to run completely offline in CI/CD pipelines. We verify model integrity, US geocode boundaries, corridor math, greedy vs DP optimality, 500-mile boundary feasibility, unreachable gap diagnosis, and Decimal calculation precision across multiple segments.
>
> The code is cleanly structured, formatted, and pushed to GitHub at `https://github.com/akshitaa18/fuel-route-optimizer`.
>
> Thank you for your time, and I look forward to discussing the architecture with your team!"
