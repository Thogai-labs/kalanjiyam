#!/usr/bin/env python3
"""Prune historical raw page OCR payload keys (projects/*/ocr/*.json.gz) from S3 / VersityGW.

Under Strategy B (Unified PageDocument Model), bounding box coordinates are derived
dynamically from PageDocument revision payloads. This script safely reclaims disk space
by deleting historical redundant /ocr/ storage objects.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import kalanjiyam
from kalanjiyam.utils.storage import get_storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Optional project_slug to target (default: all projects)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Execute deletions. Without this flag, the script runs in dry-run mode.",
    )
    args = parser.parse_args()

    dry_run = not args.force
    env_name = os.environ.get("KALANJIYAM_ENVIRONMENT", "development")
    app = kalanjiyam.create_app(env_name)

    with app.app_context():
        storage = get_storage()
        prefix = f"projects/{args.project}/" if args.project else "projects/"
        print(f"[*] Scanning storage objects under '{prefix}' for historical OCR payloads...", flush=True)

        ocr_keys: list[tuple[str, int]] = []
        total_bytes = 0

        for key, size in storage.list_keys(prefix):
            if "/ocr/" in key and key.endswith(".json.gz"):
                ocr_keys.append((key, size))
                total_bytes += size

        if not ocr_keys:
            print("No historical OCR payload objects found matching pattern.")
            return

        mb = total_bytes / (1024 * 1024)
        print(f"[*] Found {len(ocr_keys):,} historical OCR objects totaling {mb:.2f} MB.")

        if dry_run:
            print("\n[DRY RUN] No files deleted. Pass '--force' to execute deletion.")
            print("Sample keys to be pruned:")
            for key, size in ocr_keys[:10]:
                print(f"  - {key} ({size / 1024:.1f} KB)")
            if len(ocr_keys) > 10:
                print(f"  ... and {len(ocr_keys) - 10} more key(s).")
            return

        print("\n[*] Executing deletion of historical OCR objects...", flush=True)
        deleted_count = 0
        reclaimed_bytes = 0

        for key, size in ocr_keys:
            if storage.delete(key):
                deleted_count += 1
                reclaimed_bytes += size

        reclaimed_mb = reclaimed_bytes / (1024 * 1024)
        print("=" * 60)
        print(f"CLEANUP COMPLETE:")
        print(f"  Deleted Objects  : {deleted_count:,} files")
        print(f"  Reclaimed Space  : {reclaimed_mb:.2f} MB ({reclaimed_bytes:,} bytes)")
        print("=" * 60)


if __name__ == "__main__":
    main()
