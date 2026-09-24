#!/usr/bin/env python3
"""Attach deterministic evidence-grounded summaries to an existing run.

Pure computation over already-collected evidence (no network, no re-crawl): adds
``profile["summary"]`` to every profile and to the profile embedded in each
terminal envelope. Use after ``run_competition_batch.py`` when you want to add
summaries without re-running the batch. New batch runs include summaries
automatically.

    python scripts/add_summaries.py --profiles out/profiles.jsonl --envelopes out/envelopes.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.synthesis import summarize_profile  # noqa: E402


def _rewrite(path: Path, transform) -> int:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        transform(row)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(path)
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profiles", type=Path, default=Path("out/profiles.jsonl"))
    ap.add_argument("--envelopes", type=Path, default=Path("out/envelopes.jsonl"))
    args = ap.parse_args()

    n = _rewrite(args.profiles, lambda p: p.__setitem__("summary", summarize_profile(p)))
    print(f"Added summaries to {n} profiles")
    if args.envelopes.exists():
        m = _rewrite(args.envelopes, lambda e: e.get("profile", {}).__setitem__("summary", summarize_profile(e.get("profile", {}))))
        print(f"Added summaries to {m} envelopes")


if __name__ == "__main__":
    main()
