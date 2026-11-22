import os
import json
import time
import requests
import pandas as pd
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
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

        self.stdout.write(self.style.NOTICE("=== Starting Fuel Station Import Pipeline ==="))
        self.stdout.write(f"Source CSV: {csv_path}")

        if not os.path.exists(csv_path):
            raise CommandError(f"CSV file not found at: {csv_path}")

        # 1. Read CSV using Pandas
        try:
            df = pd.read_csv(csv_path, dtype={'OPIS Truckstop ID': str, 'Rack ID': str})
        except Exception as e:
            raise CommandError(f"Failed to read CSV with pandas: {e}")

        total_rows = len(df)
        self.stdout.write(f"Total raw rows read from CSV: {total_rows}")

        # 2. Validate columns
        expected_cols = [
            'OPIS Truckstop ID', 'Truckstop Name', 'Address',
            'City', 'State', 'Rack ID', 'Retail Price'
        ]
        missing_cols = [c for c in expected_cols if c not in df.columns]
        if missing_cols:
            raise CommandError(f"CSV is missing required columns: {missing_cols}")

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
            c.query: (c.latitude, c.longitude, c.is_exact, c.provider)
            for c in GeocodeCache.objects.all()
        }
        self.stdout.write(f"Loaded {len(db_cache)} entries from GeocodeCache database table")

        # 3. Process, clean, and deduplicate
        stats = {
            'total_rows': total_rows,
            'valid_records': 0,
            'invalid_price': 0,
            'invalid_address': 0,
            'non_us_canadian': 0,
            'duplicates_removed': 0,
            'exact_resolved': 0,
            'approx_resolved': 0,
            'unresolved': 0,
            'cached_hits': 0,
            'live_geocoded': 0,
            'saved_to_db': 0,
        }

        # Track unique stations: key = (station_id, address_clean, city_clean, state_clean)
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

            # Validate price: must be positive and within reasonable diesel limits ($0.50 - $25.00)
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
                prev_record = seen_stations[dedup_key]
                # If duplicate record has cheaper price, keep the lower price
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

        stats['valid_records'] = len(valid_candidates)
        self.stdout.write(
            f"Filtered candidates: {stats['valid_records']} valid US records "
            f"({stats['duplicates_removed']} duplicates merged, "
            f"{stats['non_us_canadian']} Canadian records skipped, "
            f"{stats['invalid_price']} invalid prices, "
            f"{stats['invalid_address']} invalid addresses)"
        )

        # 4. Geocode resolution with precision tracking
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
            is_exact = False
            source = 'city_centroid'

            # 4a. Check DB GeocodeCache
            if full_query in db_cache:
                lat, lon, is_exact, source = db_cache[full_query]
                stats['cached_hits'] += 1
            elif city_query in db_cache:
                lat, lon, is_exact, source = db_cache[city_query]
                is_exact = False
                source = 'city_centroid'
                stats['cached_hits'] += 1
            # 4b. Check offline JSON cache
            elif full_query in offline_cache:
                c = offline_cache[full_query]
                lat, lon = float(c['latitude']), float(c['longitude'])
                is_exact = bool(c.get('is_exact', False))
                source = c.get('source', 'offline_gazetteer')
                stats['cached_hits'] += 1
                db_cache[full_query] = (lat, lon, is_exact, source)
                new_geocode_cache_entries.append(
                    GeocodeCache(
                        query=full_query,
                        latitude=lat,
                        longitude=lon,
                        formatted_address=f"{city}, {state}",
                        provider=source,
                        is_exact=is_exact
                    )
                )
            elif city_query in offline_cache:
                c = offline_cache[city_query]
                lat, lon = float(c['latitude']), float(c['longitude'])
                is_exact = False
                source = 'city_centroid'
                stats['cached_hits'] += 1
                db_cache[city_query] = (lat, lon, is_exact, source)
                new_geocode_cache_entries.append(
                    GeocodeCache(
                        query=city_query,
                        latitude=lat,
                        longitude=lon,
                        formatted_address=f"{city}, {state}",
                        provider=source,
                        is_exact=False
                    )
                )
            else:
                # 4c. Live geocoding fallback for uncached records (rate-limited, 1 req/sec)
                try:
                    time.sleep(1.0)
                    headers = {'User-Agent': 'FuelRouteOptimizerApp/1.0 (assessment@internal.app)'}
                    url = f"https://nominatim.openstreetmap.org/search?q={requests.utils.quote(city_query)}&format=json&limit=1"
                    resp = requests.get(url, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data and len(data) > 0:
                            lat = float(data[0]['lat'])
                            lon = float(data[0]['lon'])
                            is_exact = False
                            source = 'nominatim_city_fallback'
                            stats['live_geocoded'] += 1
                            db_cache[city_query] = (lat, lon, is_exact, source)
                            new_geocode_cache_entries.append(
                                GeocodeCache(
                                    query=city_query,
                                    latitude=lat,
                                    longitude=lon,
                                    formatted_address=data[0].get('display_name', city_query),
                                    provider=source,
                                    is_exact=False
                                )
                            )
                except Exception as ex:
                    self.stdout.write(self.style.WARNING(f"Live geocode failed for {city_query}: {ex}"))

            # Validate coordinates are within US bounds
            if (lat is not None and lon is not None and
                    (US_LAT_MIN <= lat <= US_LAT_MAX) and
                    (US_LON_MIN <= lon <= US_LON_MAX)):
                if is_exact:
                    stats['exact_resolved'] += 1
                else:
                    stats['approx_resolved'] += 1

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
                    is_exact=is_exact,
                    geocode_source=source,
                )
                stations_to_create.append(station_obj)
            else:
                stats['unresolved'] += 1
                failed_records.append(record)

        # 5. Persist to database without creating unexpected duplicates
        if clear_existing:
            self.stdout.write("Clearing existing FuelStation records as requested via --clear...")
            FuelStation.objects.all().delete()
        elif FuelStation.objects.exists():
            self.stdout.write("Existing stations found. Replacing with freshly validated records to prevent duplicates...")
            FuelStation.objects.all().delete()

        # Batch insert new geocode cache entries
        if new_geocode_cache_entries:
            unique_cache_dict = {c.query: c for c in new_geocode_cache_entries}
            GeocodeCache.objects.bulk_create(
                list(unique_cache_dict.values()),
                batch_size=batch_size,
                ignore_conflicts=True
            )
            self.stdout.write(f"Saved {len(unique_cache_dict)} geocoded queries into GeocodeCache table")

        # Bulk insert stations
        if stations_to_create:
            FuelStation.objects.bulk_create(stations_to_create, batch_size=batch_size)
            stats['saved_to_db'] = len(stations_to_create)

        elapsed = time.time() - start_time

        # 6. Meaningful import statistics report
        self.stdout.write("\n" + "=" * 65)
        self.stdout.write(self.style.SUCCESS("FUEL ROUTE OPTIMIZER - INGESTION SUMMARY REPORT"))
        self.stdout.write("=" * 65)
        self.stdout.write(f"{'Metric':<40} | {'Count':>18}")
        self.stdout.write("-" * 62)
        self.stdout.write(f"{'Total CSV records processed':<40} | {stats['total_rows']:>18,}")
        self.stdout.write(f"{'Valid unique candidates':<40} | {stats['valid_records']:>18,}")
        self.stdout.write(f"{'Invalid retail prices':<40} | {stats['invalid_price']:>18,}")
        self.stdout.write(f"{'Invalid addresses/data':<40} | {stats['invalid_address']:>18,}")
        self.stdout.write(f"{'Non-US (Canadian) records skipped':<40} | {stats['non_us_canadian']:>18,}")
        self.stdout.write(f"{'Duplicates removed / merged':<40} | {stats['duplicates_removed']:>18,}")
        self.stdout.write(f"{'Exact street coordinates resolved':<40} | {stats['exact_resolved']:>18,}")
        self.stdout.write(f"{'Approximate city-centroid coordinates':<40} | {stats['approx_resolved']:>18,}")
        self.stdout.write(f"{'Unresolved / geocoding failures':<40} | {stats['unresolved']:>18,}")
        self.stdout.write(f"{'Total stations saved to database':<40} | {stats['saved_to_db']:>18,}")
        self.stdout.write(f"{'Total processing elapsed time':<40} | {elapsed:>15.2f} sec")
        self.stdout.write("=" * 65)

        if failed_records:
            self.stdout.write(self.style.WARNING(f"\nSample of {len(failed_records)} failed records:"))
            for rec in failed_records[:5]:
                self.stdout.write(f"  - [{rec['station_id']}] {rec['name']}: {rec['address']}, {rec['city']}, {rec['state']}")
