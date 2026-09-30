import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from calibration_check import evaluate_calibration

from test_config import _config_dict


class FakeModelData:
    def __init__(self, series):
        self.series = series

    def get_series(self, gauge_alias, signal_alias, result_name):
        return self.series


class FakeMonitoringRepository:
    def __init__(self, series=None, error=None):
        self.series = series
        self.error = error
        self.warnings = []

    def get_series(self, reference):
        if self.error is not None:
            raise self.error
        return self.series


class ApiTests(unittest.TestCase):
    def test_evaluate_calibration_accepts_dict_and_returns_flat_builtin_rows(self):
        index = pd.date_range("2024-01-01", periods=3, freq="h")
        observed = pd.Series([1.0, 2.0, 3.0], index=index)
        simulated = pd.Series([1.0, 2.5, 2.0], index=index)

        rows = _evaluate_with_fakes(
            _config_dict(),
            observed,
            simulated,
        )

        rmse = next(row for row in rows if row["statistic"] == "rmse")
        self.assertEqual(rmse["gauge"], "Gate Chamber")
        self.assertEqual(rmse["signal"], "Anything User Wants")
        self.assertEqual(rmse["event"], "event")
        self.assertEqual(rmse["result"], "event")
        self.assertIsInstance(rmse["value"], float)
        self.assertEqual(rmse["warning"], "")

    def test_evaluate_calibration_accepts_json_path(self):
        index = pd.date_range("2024-01-01", periods=2, freq="h")
        observed = pd.Series([1.0, 2.0], index=index)
        simulated = pd.Series([1.0, 2.0], index=index)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "calibration.json"
            path.write_text(json.dumps(_config_dict()), encoding="utf-8")

            rows = _evaluate_with_fakes(path, observed, simulated)

        self.assertTrue(any(row["statistic"] == "rmse" for row in rows))

    def test_custom_statistics_receive_aligned_series(self):
        observed_index = pd.to_datetime([
            "2024-01-01 00:00",
            "2024-01-01 01:00",
            "2024-01-01 02:00",
        ])
        simulated_index = pd.to_datetime([
            "2024-01-01 00:30",
            "2024-01-01 01:30",
        ])
        observed = pd.Series([1.0, 3.0, 5.0], index=observed_index)
        simulated = pd.Series([2.0, 6.0], index=simulated_index)

        def custom_stats(obs, sim):
            return {"custom_bias": float((sim - obs).mean())}

        rows = _evaluate_with_fakes(
            _config_dict(),
            observed,
            simulated,
            statistics=custom_stats,
            include_builtin_statistics=False,
        )

        self.assertEqual(
            [row["statistic"] for row in rows],
            ["custom_bias"],
        )
        self.assertAlmostEqual(rows[0]["value"], 1.0)

    def test_warnings_are_returned_as_rows(self):
        index = pd.date_range("2024-01-01", periods=2, freq="h")
        simulated = pd.Series([1.0, 2.0], index=index)

        rows = _evaluate_with_fakes(
            _config_dict(),
            None,
            simulated,
            repository_error=ValueError("missing observed column"),
        )

        warning = next(row for row in rows if row["warning"])
        self.assertEqual(warning["statistic"], "")
        self.assertIsNone(warning["value"])
        self.assertIn("missing observed column", warning["warning"])

    def test_run_level_warnings_are_returned_as_rows(self):
        data = _config_dict()
        data["results"][0]["start_time"] = "2024-02-01"
        data["results"][0]["end_time"] = "2024-02-02"
        rows = _evaluate_with_fakes(
            data,
            pd.Series(dtype=float),
            pd.Series(dtype=float),
        )

        self.assertEqual(rows[0]["gauge"], "")
        self.assertEqual(rows[0]["statistic"], "")
        self.assertIn("No result overlaps event", rows[0]["warning"])


def _evaluate_with_fakes(
        config,
        observed,
        simulated,
        **kwargs,
        ):
    repository_error = kwargs.pop("repository_error", None)
    repository = FakeMonitoringRepository(observed, repository_error)
    with (
        patch("calibration_check.api.MonitoringRepository", return_value=repository),
        patch("calibration_check.api.extract_simulated", return_value=FakeModelData(simulated)),
    ):
        return evaluate_calibration(config, **kwargs)


if __name__ == "__main__":
    unittest.main()
