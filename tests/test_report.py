import tempfile
import unittest
from pathlib import Path

import pandas as pd

from calibration_check.analysis import CalibrationRun, SignalComparison
from calibration_check.models import parse_config
from calibration_check.report import export_html

from test_config import _config_dict


class ReportTests(unittest.TestCase):
    def test_static_report_contains_plot_and_statistics(self):
        config = parse_config(_config_dict(), Path.cwd())
        index = pd.date_range("2024-01-01", periods=2, freq="h")
        comparison = SignalComparison(
            "Gate Chamber",
            "event",
            "event",
            "Anything User Wants",
            pd.Series([1.0, 2.0], index=index),
            pd.Series([1.1, 1.9], index=index),
            {"rmse": 0.1},
        )
        run = CalibrationRun(config, [comparison])
        with tempfile.TemporaryDirectory() as folder:
            target = export_html(run, Path(folder) / "report.html")
            content = target.read_text(encoding="utf-8")

        self.assertIn("Anything User Wants", content)
        self.assertIn("Rmse", content)
        self.assertIn("plotly-graph-div", content)
        self.assertIn("gauge-filter", content)
        self.assertIn("event-filter", content)
        self.assertIn("Download CSV", content)
        self.assertIn("Gate_Chamber__Anything_User_Wants__event__event.csv", content)
        self.assertIn("# gauge: Gate Chamber", content)
        self.assertIn("timestamp,observed,simulated", content)


if __name__ == "__main__":
    unittest.main()
