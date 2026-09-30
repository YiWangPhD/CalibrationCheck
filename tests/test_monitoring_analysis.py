import tempfile
import unittest
from pathlib import Path

import pandas as pd

from calibration_check.analysis import analyze, calculate_metrics
from calibration_check.models import MonitoringPickleOptions
from calibration_check.monitoring import MonitoringRepository
from calibration_check.models import parse_config

from test_config import _config_dict


class FakeModelData:
    def __init__(self, series):
        self.series = series

    def get_series(self, gauge_alias, signal_alias, result_name):
        return self.series


class MonitoringAnalysisTests(unittest.TestCase):
    def test_source_is_loaded_once(self):
        config = parse_config(_config_dict(), Path.cwd())
        calls = []
        index = pd.date_range("2024-01-01", periods=3, freq="h")

        def loader(source):
            calls.append(source.alias)
            return pd.DataFrame({"Gate Position": [1.0, 2.0, 3.0]}, index=index)

        repository = MonitoringRepository(config, {"dfs0": loader})
        reference = config.gauges[0].signals[0].observed
        repository.get_series(reference)
        repository.get_series(reference)

        self.assertEqual(calls, ["gates"])

    def test_analysis_compares_generic_signal(self):
        data = _config_dict()
        data["results"][0].update({
            "start_time": "2024-01-01",
            "end_time": "2024-01-02",
        })
        config = parse_config(data, Path.cwd())
        index = pd.date_range("2024-01-01", periods=4, freq="8h")
        observed = pd.DataFrame({"Gate Position": [1.0, 2.0, 3.0, 4.0]}, index=index)
        monitoring = MonitoringRepository(config, {"dfs0": lambda source: observed})
        model = FakeModelData(pd.Series([1.0, 2.5, 2.5, 4.0], index=index))

        run = analyze(config, monitoring, model)

        self.assertEqual(len(run.comparisons), 1)
        self.assertIn("rmse", run.comparisons[0].metrics)

    def test_monitoring_pickle_can_be_created_and_used(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_path = root / "gates.dfs0"
            source_path.write_bytes(b"dfs0 bytes")
            data = _config_dict()
            data["monitoring_sources"] = {
                "gates": {"format": "dfs0", "path": str(source_path)}
            }
            config = parse_config(data, root)
            pickle_path = root / "observed_sources.pkl"
            index = pd.date_range("2024-01-01", periods=3, freq="h")
            observed = pd.DataFrame(
                {"Gate Position": [1.0, 2.0, 3.0]},
                index=index,
            )

            MonitoringRepository(
                config,
                {"dfs0": lambda source: observed},
                MonitoringPickleOptions(
                    create_or_update=True,
                    path=pickle_path,
                ),
            )
            repository = MonitoringRepository(
                config,
                {"dfs0": lambda source: self.fail("DFS0 loader should not run")},
                MonitoringPickleOptions(use=True, path=pickle_path),
            )

            series = repository.get_series(config.gauges[0].signals[0].observed)

        self.assertEqual(series.tolist(), [1.0, 2.0, 3.0])

    def test_missing_pickle_column_becomes_analysis_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_path = root / "gates.dfs0"
            source_path.write_bytes(b"dfs0 bytes")
            data = _config_dict()
            data["monitoring_sources"] = {
                "gates": {"format": "dfs0", "path": str(source_path)}
            }
            config = parse_config(data, root)
            pickle_path = root / "observed_sources.pkl"
            index = pd.date_range("2024-01-01", periods=3, freq="h")
            observed = pd.DataFrame({"Other Column": [1.0, 2.0, 3.0]}, index=index)
            MonitoringRepository(
                config,
                {"dfs0": lambda source: observed},
                MonitoringPickleOptions(create_or_update=True, path=pickle_path),
            )
            repository = MonitoringRepository(
                config,
                pickle_options=MonitoringPickleOptions(use=True, path=pickle_path),
            )
            model = FakeModelData(pd.Series([1.0, 2.0, 3.0], index=index))

            run = analyze(config, repository, model)

        self.assertIn("Monitoring column 'Gate Position' was not found", run.warnings[0])

    def test_metrics_do_not_require_signal_type(self):
        index = pd.date_range("2024-01-01", periods=3, freq="h")
        observed = pd.Series([1.0, 2.0, 3.0], index=index)
        simulated = pd.Series([1.0, 3.0, 3.0], index=index)

        metrics = calculate_metrics(observed, simulated)

        self.assertAlmostEqual(metrics["mean_error"], 1 / 3)
        self.assertIn("integral_difference", metrics)


if __name__ == "__main__":
    unittest.main()
