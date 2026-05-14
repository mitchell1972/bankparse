#!/usr/bin/env python3
"""
Scrape accounting & bookkeeping firms using Google Places API (New).
Outputs results to CSV.

Usage:
    python scripts/scrape_accountants.py                          # All AL counties
    python scripts/scrape_accountants.py --county "Autauga"       # Single county
    python scripts/scrape_accountants.py --state TX --county Harris  # Other state
"""

import argparse
import csv
import os
import sys
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

API_KEY = os.getenv("GOOGLE_API_KEY", "")
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

SEARCH_QUERIES = [
    "accounting firms in {location}",
    "bookkeeping services in {location}",
    "CPA firms in {location}",
    "tax preparation services in {location}",
]

FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.googleMapsUri",
    "places.businessStatus",
    "places.types",
])

ALABAMA_COUNTIES = [
    "Autauga", "Baldwin", "Barbour", "Bibb", "Blount", "Bullock", "Butler",
    "Calhoun", "Chambers", "Cherokee", "Chilton", "Choctaw", "Clarke", "Clay",
    "Cleburne", "Coffee", "Colbert", "Conecuh", "Coosa", "Covington",
    "Crenshaw", "Cullman", "Dale", "Dallas", "DeKalb", "Elmore", "Escambia",
    "Etowah", "Fayette", "Franklin", "Geneva", "Greene", "Hale", "Henry",
    "Houston", "Jackson", "Jefferson", "Lamar", "Lauderdale", "Lawrence",
    "Lee", "Limestone", "Lowndes", "Macon", "Madison", "Marengo", "Marion",
    "Marshall", "Mobile", "Monroe", "Montgomery", "Morgan", "Perry",
    "Pickens", "Pike", "Randolph", "Russell", "Shelby", "St. Clair",
    "Sumter", "Talladega", "Tallapoosa", "Tuscaloosa", "Walker",
    "Washington", "Wilcox", "Winston",
]


def search_places(query: str, page_token: str = None) -> dict:
    """Call Google Places API Text Search."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": FIELD_MASK + ",nextPageToken",
    }
    body = {
        "textQuery": query,
        "languageCode": "en",
    }
    if page_token:
        body["pageToken"] = page_token

    resp = requests.post(PLACES_URL, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def extract_firm(place: dict, county: str, state: str) -> dict:
    """Extract structured data from a Places API result."""
    return {
        "name": place.get("displayName", {}).get("text", ""),
        "address": place.get("formattedAddress", ""),
        "phone": place.get("nationalPhoneNumber", ""),
        "intl_phone": place.get("internationalPhoneNumber", ""),
        "website": place.get("websiteUri", ""),
        "google_maps_url": place.get("googleMapsUri", ""),
        "business_status": place.get("businessStatus", ""),
        "types": ", ".join(place.get("types", [])),
        "place_id": place.get("id", ""),
        "county": county,
        "state": state,
    }


def scrape_county(county: str, state: str) -> list[dict]:
    """Scrape all accounting/bookkeeping firms for a single county."""
    location = f"{county} County, {state}"
    seen_ids = set()
    results = []

    for query_template in SEARCH_QUERIES:
        query = query_template.format(location=location)
        print(f"  Searching: {query}", flush=True)

        page_token = None
        while True:
            try:
                data = search_places(query, page_token)
            except requests.exceptions.HTTPError as e:
                print(f"    API error: {e}", flush=True)
                break

            for place in data.get("places", []):
                pid = place.get("id", "")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    results.append(extract_firm(place, county, state))

            page_token = data.get("nextPageToken")
            if not page_token:
                break
            time.sleep(0.5)

        # Rate limit between queries
        time.sleep(0.3)

    return results


def main():
    parser = argparse.ArgumentParser(description="Scrape accounting firms via Google Places API")
    parser.add_argument("--state", default="Alabama", help="State name (default: Alabama)")
    parser.add_argument("--county", help="Single county to scrape (omit for all AL counties)")
    parser.add_argument("--output", default="accountants.csv", help="Output CSV filename")
    args = parser.parse_args()

    if not API_KEY:
        print("ERROR: GOOGLE_API_KEY not set in .env file")
        sys.exit(1)

    counties = [args.county] if args.county else ALABAMA_COUNTIES
    output_path = Path(__file__).resolve().parent.parent / args.output

    fieldnames = ["name", "address", "phone", "intl_phone", "website",
                  "google_maps_url", "business_status", "types", "place_id",
                  "county", "state"]

    # Write header immediately
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

    total = 0
    for i, county in enumerate(counties, 1):
        print(f"\n[{i}/{len(counties)}] Scraping {county} County, {args.state}...", flush=True)
        firms = scrape_county(county, args.state)
        total += len(firms)
        print(f"  Found {len(firms)} unique firms (running total: {total})", flush=True)

        # Append after each county so data is never lost
        if firms:
            with open(output_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writerows(firms)

    print(f"\nDone! {total} firms saved to {output_path}", flush=True)


if __name__ == "__main__":
    main()
