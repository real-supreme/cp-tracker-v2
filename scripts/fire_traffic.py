# Copyright 2022–2025 Emmanuel Smiju (real-supreme)
# SPDX-License-Identifier: AGPL-3.0-only
import argparse
import json
import ssl
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
BASE_URL = "http://localhost:8000/api/v1"
TOKEN = "secret"  # Bearer token from API_CLIENT_KEYS
WORKERS = 50      # Number of parallel requests

# Ignore self-signed cert warnings for localhost
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def fire_request(entity_type: str, tag: str) -> bool:
    """Fire a single GET request to the local API."""
    tag = tag.strip().lstrip("#")
    url = f"{BASE_URL}/{entity_type}/{tag}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            # 200 = already in DB, 202 = enqueued. Both are success.
            return resp.status in (200, 202)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.reason}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


def run(filepath: str, entity_type: str, workers: int) -> None:
    """Read a JSON array of tags and fire requests to the API."""
    try:
        with open(filepath, "r") as f:
            tags = json.load(f)
    except FileNotFoundError:
        print(f"Error: {filepath} not found.")
        sys.exit(1)
    except json.JSONDecodeError:
        print(f"Error: {filepath} is not valid JSON.")
        sys.exit(1)

    if not isinstance(tags, list):
        print(f"Error: {filepath} must contain a JSON array of strings.")
        sys.exit(1)

    total = len(tags)
    print(f"Firing {total} {entity_type} requests with {workers} workers...")

    success = 0
    failed = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fire_request, entity_type, t): t for t in tags}
        
        for i, future in enumerate(as_completed(futures), 1):
            if future.result():
                success += 1
            else:
                failed += 1

            # Print progress every 50 items, or at the end
            if i % 50 == 0 or i == total:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                print(f"Progress: {i}/{total} ({rate:.1f} req/s) | Success: {success} | Failed: {failed}", flush=True)

    elapsed = time.time() - start_time
    print(f"\nDone! Fired {total} requests in {elapsed:.2f}s ({(total/elapsed):.1f} req/s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fire requests at the local Clash Panda Tracker API.")
    parser.add_argument("file", help="Path to JSON file containing array of tags")
    parser.add_argument("type", choices=["clans", "players", "wars"], help="Entity type to request")
    parser.add_argument("--workers", type=int, default=50, help="Number of parallel workers")
    args = parser.parse_args()

    run(args.file, args.type, args.workers)