#!/usr/bin/env python3
"""Calculate and compare storage savings per project/book and export to CSV."""

import csv
import gzip
import os
import sys
from collections import defaultdict
from dataclasses import dataclass

import kalanjiyam
from kalanjiyam.utils.storage import get_storage


@dataclass
class ProjectStats:
    project_slug: str
    ocr_files: int = 0
    revision_files: int = 0
    compressed_bytes: int = 0
    uncompressed_bytes: int = 0

    @property
    def total_files(self) -> int:
        return self.ocr_files + self.revision_files

    @property
    def comp_mb(self) -> float:
        return self.compressed_bytes / (1024 * 1024)

    @property
    def uncomp_mb(self) -> float:
        return self.uncompressed_bytes / (1024 * 1024)

    @property
    def saved_mb(self) -> float:
        return self.uncomp_mb - self.comp_mb

    @property
    def reduction_pct(self) -> float:
        return ((self.uncomp_mb - self.comp_mb) / self.uncomp_mb * 100) if self.uncomp_mb > 0 else 0.0

    @property
    def ratio(self) -> float:
        return (self.uncomp_mb / self.comp_mb) if self.compressed_bytes > 0 else 0.0


def main(output_csv: str = "storage_savings_by_project.csv"):
    env_name = os.environ.get("KALANJIYAM_ENVIRONMENT", "development")
    app = kalanjiyam.create_app(env_name)

    with app.app_context():
        storage = get_storage()
        print("[*] Scanning S3 / VersityGW payload objects by project...\n", flush=True)

        project_map: dict[str, ProjectStats] = defaultdict(lambda: ProjectStats(project_slug=""))

        for key, size in storage.list_keys("projects/"):
            if not key.endswith(".json.gz"):
                continue

            parts = key.split("/")
            if len(parts) < 2:
                continue

            project_slug = parts[1]
            stats = project_map[project_slug]
            stats.project_slug = project_slug

            if "/ocr/" in key:
                stats.ocr_files += 1
            elif "/revisions/" in key:
                stats.revision_files += 1

            stats.compressed_bytes += size

            try:
                compressed_bytes = storage.read_bytes(key)
                uncompressed_bytes = gzip.decompress(compressed_bytes)
                stats.uncompressed_bytes += len(uncompressed_bytes)
            except Exception as err:
                print(f"  [WARNING] Could not decompress {key}: {err}", flush=True)

        if not project_map:
            print("No .json.gz payload files found in storage.")
            return

        total_comp_bytes = sum(p.compressed_bytes for p in project_map.values())
        total_uncomp_bytes = sum(p.uncompressed_bytes for p in project_map.values())
        total_files = sum(p.total_files for p in project_map.values())

        # Write CSV report
        csv_path = os.path.abspath(output_csv)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "project_slug",
                "ocr_files",
                "revision_files",
                "total_files",
                "old_pg_uncompressed_bytes",
                "old_pg_uncompressed_mb",
                "new_s3_compressed_bytes",
                "new_s3_compressed_mb",
                "saved_mb",
                "reduction_percent",
                "compression_ratio",
            ])

            for stats in sorted(project_map.values(), key=lambda x: x.uncompressed_bytes, reverse=True):
                writer.writerow([
                    stats.project_slug,
                    stats.ocr_files,
                    stats.revision_files,
                    stats.total_files,
                    stats.uncompressed_bytes,
                    f"{stats.uncomp_mb:.4f}",
                    stats.compressed_bytes,
                    f"{stats.comp_mb:.4f}",
                    f"{stats.saved_mb:.4f}",
                    f"{stats.reduction_pct:.2f}%",
                    f"{stats.ratio:.2f}x",
                ])

        # Print terminal report
        comp_mb = total_comp_bytes / (1024 * 1024)
        uncomp_mb = total_uncomp_bytes / (1024 * 1024)
        saved_mb = uncomp_mb - comp_mb
        reduction_pct = ((uncomp_mb - comp_mb) / uncomp_mb * 100) if uncomp_mb > 0 else 0
        ratio = (uncomp_mb / comp_mb) if total_comp_bytes > 0 else 0

        print("=" * 80)
        print("          STORAGE PAYLOAD COMPRESSION REPORT (PER PROJECT)          ")
        print("=" * 80)
        print(f"{'PROJECT SLUG':<35} | {'FILES':<6} | {'OLD PG (MB)':<11} | {'S3 GZ (MB)':<10} | {'SAVED':<8}")
        print("-" * 80)

        for stats in sorted(project_map.values(), key=lambda x: x.uncompressed_bytes, reverse=True)[:15]:
            slug_disp = stats.project_slug[:33] + ".." if len(stats.project_slug) > 35 else stats.project_slug
            print(f"{slug_disp:<35} | {stats.total_files:<6} | {stats.uncomp_mb:<11.2f} | {stats.comp_mb:<10.2f} | {stats.reduction_pct:<7.1f}%")

        if len(project_map) > 15:
            print(f"... and {len(project_map) - 15} more project(s)")

        print("=" * 80)
        print("TOTAL SUMMARY:")
        print(f"  Total Projects Analyzed : {len(project_map):,} books")
        print(f"  Total Payload Files     : {total_files:,} files")
        print(f"  Old PostgreSQL Raw Size  : {uncomp_mb:.2f} MB ({total_uncomp_bytes:,} bytes)")
        print(f"  New S3 json.gz Size      : {comp_mb:.2f} MB ({total_comp_bytes:,} bytes)")
        print(f"  Storage Space Saved      : {saved_mb:.2f} MB ({reduction_pct:.1f}% reduction)")
        print(f"  Compression Ratio        : {ratio:.1f}x smaller than PostgreSQL")
        print(f"  CSV Detailed Export      : {csv_path}")
        print("=" * 80)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "storage_savings_by_project.csv"
    main(out)
