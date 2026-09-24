#!/usr/bin/env python3
"""Intersect the frozen Signalpost universe with the live BRREG bulk registry.

The frozen 2025 universe includes entities that have since been deregistered
from the live registry. ``run_competition_batch.py`` requires every requested
organisation number to resolve in the bulk snapshot, so a reproducible entry
selects from universe ∩ current-registry. This writes that intersection.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path


def _bulk_org_numbers(bulk: Path) -> set[str]:
    present: set[str] = set()
    with gzip.open(bulk, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        idx = header.index("organisasjonsnummer")
        for row in reader:
            if row:
                present.add(row[idx].strip())
    return present


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--universe", required=True, help="Frozen universe .jsonl(.gz)")
    ap.add_argument("--bulk", required=True, help="Live BRREG bulk CSV .gz")
    ap.add_argument("--output", required=True, help="Output present-universe .jsonl.gz")
    args = ap.parse_args()

    present = _bulk_org_numbers(Path(args.bulk))
    universe = Path(args.universe)
    opener = gzip.open if universe.suffix == ".gz" else open
    total = kept = 0
    with opener(universe, "rt", encoding="utf-8") as src, \
            gzip.open(args.output, "wt", encoding="utf-8") as out:
        for line in src:
            if not line.strip():
                continue
            total += 1
            row = json.loads(line)
            if row["organisation_number"] in present:
                out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                kept += 1
    print(f"bulk registry orgs: {len(present):,}")
    print(f"universe total={total:,} present={kept:,} missing={total - kept:,}")
    print(f"Wrote {kept:,} present companies to {args.output}")


if __name__ == "__main__":
    main()
