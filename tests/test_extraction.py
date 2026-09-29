import unittest
from pathlib import Path

from calibration_check.extraction import build_res1d2excel_spec
from calibration_check.models import parse_config

from test_config import _config_dict


class ExtractionPlanTests(unittest.TestCase):
    def test_direct_signal_becomes_explicit_element(self):
        config = parse_config(_config_dict(), Path.cwd())

        plan = build_res1d2excel_spec(config)

        row = plan.spec["gate"][0]
        self.assertEqual(row["muid"], "Gate1")
        self.assertEqual(row["quantity"], "GateLevel")
        self.assertEqual(
            plan.aliases[("Gate Chamber", "Anything User Wants")],
            "Gate Chamber :: Anything User Wants",
        )

    def test_combined_signal_creates_explicit_terms(self):
        data = _config_dict()
        data["gauges"][0]["signals"][0]["simulated"] = {
            "terms": [
                {"operation": "+", "element_type": "link", "muid": "L1", "quantity": "Discharge"},
                {"operation": "-", "element_type": "link", "muid": "L2", "quantity": "Discharge"},
            ]
        }
        config = parse_config(data, Path.cwd())

        plan = build_res1d2excel_spec(config)

        self.assertEqual(len(plan.spec["link"]), 2)
        self.assertEqual(plan.spec["combined"][0]["terms"][1]["op"], "-")


if __name__ == "__main__":
    unittest.main()
