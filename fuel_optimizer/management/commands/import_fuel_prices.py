import os
import json
import time
import requests
import pandas as pd
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.conf import settings
from fuel_optimizer.models import FuelStation, GeocodeCache

# Non-US (Canadian) province codes present in dataset
CANADIAN_PROVINCES = {
    'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT'
}

# Approximate bounding box for the United States (including AK, HI)
US_LAT_MIN, US_LAT_MAX = 18.0, 72.0
US_LON_MIN, US_LON_MAX = -170.0, -65.0


class Command(BaseCommand):
    help = (
        "Import, validate, deduplicate, geocode, and store fuel price records from CSV."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            type=str,
            default=os.path.join(settings.BASE_DIR, 'data', 'fuel-prices-for-be-assessment.csv'),
            help="Path to fuel prices CSV file"
        )
        parser.add_argument(
            '--cache',
            type=str,
            default=os.path.join(settings.BASE_DIR, 'data', 'station_coordinates_cache.json'),
            help="Path to preprocessed offline coordinate cache JSON"
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help="Clear existing FuelStation records before importing"
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help="Database bulk_create batch size"
        )

    def handle(self, *args, **options):
        start_time = time.time()
        csv_path = options['csv']
        cache_path = options['cache']
        clear_existing = options['clear']
        batch_size = options['batch_size']

        self.stdout.write(self.style.NOTICE(f"=== Starting Fuel Station Import Pipeline ==="))
        self.stdout.write(f"Source CSV: {csv_path}")

        if not os.path.exists(csv_path):
            self.stderr.write(self.style.ERROR(f"CSV file not found at: {csv_path}"))
            return

        # 1. Read CSV using Pandas
        try:
            df = pd.read_csv(csv_path, dtype={'OPIS Truckstop ID': str, 'Rack ID': str})
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to read CSV with pandas: {e}"))
            return

        total_rows = len(df)
        self.stdout.write(f"Total raw rows read from CSV: {total_rows}")

        # 2. Validate columns
        expected_cols = [
            'OPIS Truckstop ID', 'Truckstop Name', 'Address',
            'City', 'State', 'Rack ID', 'Retail Price'
        ]
        missing_cols = [c for c in expected_cols if c not in df.columns]
        if missing_cols:
            self.stderr.write(self.style.ERROR(f"CSV is missing required columns: {missing_cols}"))
            return

        # Load offline geocode cache if available
        offline_cache = {}
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    offline_cache = json.load(f)
                self.stdout.write(f"Loaded {len(offline_cache)} offline coordinate entries from {cache_path}")
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Could not load offline cache: {e}"))

        # Preload DB geocode cache to avoid individual queries
        db_cache = {
            c.query: (c.latitude, c.longitude)
            for c in GeocodeCache.objects.all()
        }
        self.stdout.write(f"Loaded {len(db_cache)} entries from GeocodeCache database table")

        # 3. Process, clean, and deduplicate
        stats = {
            'total_rows': total_rows,
            'invalid_price': 0,
            'invalid_address': 0,
            'non_us_canadian': 0,
            'duplicates_removed': 0,
            'successfully_geocoded': 0,
            'failed_geocoded': 0,
            'cached_hits': 0,
            'live_geocoded': 0,
            'saved_to_db': 0,
        }

        # Track unique stations: key = (opis_id, address_clean, city_clean, state_clean)
        # In case of duplicate IDs, keep the best (e.g. lowest price or non-null name)
        seen_stations = {}
        valid_candidates = []

        for idx, row in df.iterrows():
            opis_id_raw = row['OPIS Truckstop ID']
            name = str(row['Truckstop Name']).strip() if pd.notnull(row['Truckstop Name']) else ''
            address = str(row['Address']).strip() if pd.notnull(row['Address']) else ''
            city = str(row['City']).strip() if pd.notnull(row['City']) else ''
            state = str(row['State']).strip().upper() if pd.notnull(row['State']) else ''
            rack_id_raw = row['Rack ID']
            price_raw = row['Retail Price']

            # Validate OPIS ID
            try:
                station_id = int(float(opis_id_raw))
            except (ValueError, TypeError):
                stats['invalid_address'] += 1
                continue

            # Validate Rack ID
            rack_id = None
            if pd.notnull(rack_id_raw):
                try:
                    rack_id = int(float(rack_id_raw))
                except (ValueError, TypeError):
                    rack_id = None

            # Validate price
            try:
                fuel_price = float(price_raw)
                if fuel_price <= 0.0 or fuel_price > 25.0:
                    stats['invalid_price'] += 1
                    continue
            except (ValueError, TypeError):
                stats['invalid_price'] += 1
                continue

            # Validate Address & State
            if not address or not city or not state:
                stats['invalid_address'] += 1
                continue

            # Flag Canadian / Non-US records
            if state in CANADIAN_PROVINCES:
                stats['non_us_canadian'] += 1
                continue

            # Deduplication key
            dedup_key = (station_id, address.upper(), city.upper(), state)
            if dedup_key in seen_stations:
                stats['duplicates_removed'] += 1
                # If duplicate has cheaper price, update it
                prev_record = seen_stations[dedup_key]
                if fuel_price < prev_record['fuel_price']:
                    prev_record['fuel_price'] = fuel_price
                    prev_record['name'] = name or prev_record['name']
                continue

            record = {
                'station_id': station_id,
                'name': name or f"Truck Stop #{station_id}",
                'address': address,
                'city': city,
                'state': state,
                'rack_id': rack_id,
                'fuel_price': fuel_price,
            }
            seen_stations[dedup_key] = record
            valid_candidates.append(record)

        self.stdout.write(
            f"Filtered candidates: {len(valid_candidates)} US records "
            f"({stats['duplicates_removed']} duplicates removed, "
            f"{stats['non_us_canadian']} Canadian records skipped, "
            f"{stats['invalid_price']} invalid prices, "
            f"{stats['invalid_address']} invalid addresses)"
        )

        # 4. Geocode resolution
        stations_to_create = []
        new_geocode_cache_entries = []
        failed_records = []

        for record in valid_candidates:
            address = record['address']
            city = record['city']
            state = record['state']

            full_query = f"{address}, {city}, {state}".upper()
            city_query = f"{city}, {state}".upper()

            lat, lon = None, None

            # 4a. Check DB GeocodeCache
            if full_query in db_cache:
                lat, lon = db_cache[full_query]
                stats['cached_hits'] += 1
            elif city_query in db_cache:
                lat, lon = db_cache[city_query]
                stats['cached_hits'] += 1
            # 4b. Check offline JSON cache
            elif full_query in offline_cache:
                c = offline_cache[full_query]
                lat, lon = float(c['latitude']), float(c['longitude'])
                stats['cached_hits'] += 1
                # Stage for DB caching
                db_cache[full_query] = (lat, lon)
                new_geocode_cache_entries.append(
                    GeocodeCache(
                        query=full_query,
                        latitude=lat,
                        longitude=lon,
                        formatted_address=f"{city}, {state}",
                        provider='offline_us_gazetteer'
                    )
                )
            elif city_query in offline_cache:
                c = offline_cache[city_query]
                lat, lon = float(c['latitude']), float(c['longitude'])
                stats['cached_hits'] += 1
                db_cache[city_query] = (lat, lon)
                new_geocode_cache_entries.append(
                    GeocodeCache(
                        query=city_query,
                        latitude=lat,
                        longitude=lon,
                        formatted_address=f"{city}, {state}",
                        provider='offline_us_gazetteer'
                    )
                )
            else:
                # 4c. Live geocoding fallback with Nominatim
                try:
                    time.sleep(1.0)  # Respect rate limit
                    headers = {'User-Agent': 'FuelRouteOptimizer/1.0 (assessment@internal.app)'}
                    url = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(city_query)}&format=json&limit=1"
                    resp = requests.get(url, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data and len(data) > 0:
                            lat = float(data[0]['lat'])
                            lon = float(data[0]['lon'])
                            stats['live_geocoded'] += 1
                            db_cache[city_query] = (lat, lon)
                            new_geocode_cache_entries.append(
                                GeocodeCache(
                                    query=city_query,
                                    latitude=lat,
                                    longitude=lon,
                                    formatted_address=data[0].get('display_name', city_query),
                                    provider='nominatim'
                                )
                            )
                except Exception as ex:
                    self.stdout.write(self.style.WARNING(f"Live geocode failed for {city_query}: {ex}"))

            # Validate coordinates are within US bounds
            if lat is not None and lon is not None and (US_LAT_MIN <= lat <= US_LAT_MAX) and (US_LON_MIN <= lon <= US_LON_MAX):
                stats['successfully_geocoded'] += 1
                station_obj = FuelStation(
                    station_id=record['station_id'],
                    station_name=record['name'],
                    address=record['address'],
                    city=record['city'],
                    state=record['state'],
                    rack_id=record['rack_id'],
                    fuel_price=Decimal(f"{record['fuel_price']:.4f}"),
                    latitude=lat,
                    longitude=lon,
                )
                stations_to_create.append(station_obj)
            else:
                stats['failed_geocoded'] += 1
                failed_records.append(record)

        # 5. Persist to database
        if clear_existing:
            self.stdout.write("Clearing existing FuelStation records as requested...")
            FuelStation.objects.all().delete()

        # Batch insert new geocode cache entries
        if new_geocode_cache_entries:
            # Deduplicate by query before bulk insert
            unique_cache_dict = {c.query: c for c in new_geocode_cache_entries}
            GeocodeCache.objects.bulk_create(
                list(unique_cache_dict.values()),
                batch_size=batch_size,
                ignore_conflicts=True
            )
            self.stdout.write(f"Saved {len(unique_cache_dict)} geocoded queries into GeocodeCache table")

        # Bulk insert stations
        if stations_to_create:
            # If not clear_existing, we can clear or insert
            if not clear_existing and FuelStation.objects.exists():
                self.stdout.write("Existing stations found. Replacing with freshly processed dataset...")
                FuelStation.objects.all().delete()

            FuelStation.objects.bulk_create(stations_to_create, batch_size=batch_size)
            stats['saved_to_db'] = len(stations_to_create)

        elapsed = time.time() - start_time

        # 6. Detailed summary report
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("FUEL ROUTE OPTIMIZER - INGESTION SUMMARY REPORT"))
        self.stdout.write("=" * 60)
        self.stdout.write(f"{'Metric':<35} | {'Count':>15}")
        self.stdout.write("-" * 55)
        self.stdout.write(f"{'Total CSV records processed':<35} | {stats['total_rows']:>15,}")
        self.stdout.write(f"{'Duplicates removed / merged':<35} | {stats['duplicates_removed']:>15,}")
        self.stdout.write(f"{'Invalid retail prices':<35} | {stats['invalid_price']:>15,}")
        self.stdout.write(f"{'Invalid addresses/data':<35} | {stats['invalid_address']:>15,}")
        self.stdout.write(f"{'Non-US (Canadian) records':<35} | {stats['non_us_canadian']:>15,}")
        self.stdout.write(f"{'Successfully geocoded':<35} | {stats['successfully_geocoded']:>15,}")
        self.stdout.write(f"{'  - Cache hits (offline/DB)':<35} | {stats['cached_hits']:>15,}")
        self.stdout.write(f"{'  - Live geocoding fallback':<35} | {stats['live_geocoded']:>15,}")
        self.stdout.write(f"{'Failed geocoding records':<35} | {stats['failed_geocoded']:>15,}")
        self.stdout.write(f"{'Stations stored in database':<35} | {stats['saved_to_db']:>15,}")
        self.stdout.write(f"{'Total processing elapsed time':<35} | {elapsed:>12.2f} sec")
        self.stdout.write("=" * 60)

        if failed_records:
            self.stdout.write(self.style.WARNING(f"\nSample of {len(failed_records)} failed records:"))
            for rec in failed_records[:5]:
                self.stdout.write(f"  - [{rec['station_id']}] {rec['name']}: {rec['address']}, {rec['city']}, {rec['state']}")
