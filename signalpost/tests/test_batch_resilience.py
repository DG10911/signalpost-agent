"""The batch must return a terminal result for every requested organisation
number, even when some are absent from the frozen registry bulk snapshot
(e.g. deregistered since the universe was frozen). It must never abort."""

from __future__ import annotations

import csv
import gzip
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.batch import profiles_from_bulk, terminal_envelope, validate_envelopes  # noqa: E402


def _write_bulk(path: Path, orgs: list[str]) -> None:
    fields = ["organisasjonsnummer", "navn", "organisasjonsform.kode",
              "sisteInnsendteAarsregnskap", "konkurs", "underAvvikling"]
    rows = [
        {"organisasjonsnummer": org, "navn": f"Company {org}", "organisasjonsform.kode": "AS",
         "sisteInnsendteAarsregnskap": "2025", "konkurs": "false", "underAvvikling": "false"}
        for org in orgs
    ]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


class BatchResilienceTest(unittest.TestCase):
    def test_absent_org_does_not_abort_and_is_terminal_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            bulk = Path(tmp) / "bulk.csv.gz"
            _write_bulk(bulk, ["100000001", "100000002"])  # only these are present
            requested = ["100000001", "999999999", "100000002"]  # middle one absent

            # Must NOT raise.
            profiles, meta = profiles_from_bulk(bulk, requested)

            self.assertEqual([p["organisation_number"] for p in profiles], requested)
            self.assertEqual(meta["absent_from_snapshot"], 1)
            self.assertEqual(meta["selected"], 2)

            absent = next(p for p in profiles if p["organisation_number"] == "999999999")
            self.assertEqual(absent["evidence"]["registry"]["status"], "not_found")

            # Envelopes for all three must be terminal and validation must pass.
            envelopes = [
                terminal_envelope(p, run_id="t", modules=["registry", "accounting_obligation"],
                                  started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:01Z")
                for p in profiles
            ]
            result = validate_envelopes(envelopes, expected_count=3)
            self.assertTrue(result["passed"], result)
            self.assertTrue(result["checks"]["all_module_states_terminal"])
            self.assertTrue(result["checks"]["zero_silent_drops"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
