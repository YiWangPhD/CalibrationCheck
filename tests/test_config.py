import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from calibration_check.config import convert_network_ini, load_config
from calibration_check.models import parse_config


class ConfigTests(unittest.TestCase):
    def test_json_supports_arbitrary_sources_and_signal_aliases(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "config.json"
            path.write_text(json.dumps(_config_dict()), encoding="utf-8")

            config = load_config(path)

            self.assertEqual(set(config.monitoring_sources), {"all", "gates"})
            self.assertEqual(config.gauges[0].signals[0].alias, "Anything User Wants")
            self.assertEqual(
                config.gauges[0].signals[0].simulated.element.element_type,
                "gate",
            )
            self.assertEqual(config.results[0].path, root / "network.res1d")

            round_tripped = parse_config(config.to_dict(), root)
            self.assertEqual(
                round_tripped.gauges[0].signals[0].simulated.element.muid,
                "Gate1",
            )

    def test_json_supports_monitoring_pickle_options(self):
        data = _config_dict()
        data["monitoring_pickle"] = {
            "use": True,
            "path": "observed_sources.pkl",
            "create_or_update": True,
        }
        config = parse_config(data, Path.cwd())

        self.assertTrue(config.monitoring_pickle.use)
        self.assertTrue(config.monitoring_pickle.create_or_update)
        self.assertEqual(config.monitoring_pickle.path, Path.cwd() / "observed_sources.pkl")

    def test_blank_event_bounds_mean_open_ended_event(self):
        data = _config_dict()
        data["gauges"][0]["events"][0]["start"] = ""
        data["gauges"][0]["events"][0]["end"] = ""

        config = parse_config(data, Path.cwd())

        self.assertEqual(config.gauges[0].events[0].start, pd.Timestamp.min)
        self.assertEqual(config.gauges[0].events[0].end, pd.Timestamp.max)

    def test_legacy_network_ini_conversion_assumes_link_discharges(self):
        content = """
[CalibrationGauges]
gauges = []
[FolderWithResultFiles]
workfolder = results
[inputPickleFiles]
MeasuredRain = rain.dfs0
MeasuredFlow = flow.dfs0
MeasuredLevel = level.dfs0
[outputPickleFiles]
outputResList = legacy.pkl
[NetworkDictionary]
PS1 = ['RG1', 12.5, 'OutletLink', 'OverflowLink']
[TimeStampDictionary]
PS1 = [['2024-01-01', '2024-01-02']]
[ResultFileDictionary]
network.res1d = ['2024-01-01', '2024-01-02']
"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "network.ini"
            path.write_text(content, encoding="utf-8")

            converted = convert_network_ini(path).config

        gauge = converted["gauges"][0]
        signals = {item["alias"]: item for item in gauge["signals"]}
        self.assertEqual(signals["Flow"]["simulated"]["element_type"], "link")
        self.assertEqual(signals["CSO Discharge"]["simulated"]["element_type"], "link")
        self.assertEqual(signals["Level"]["simulated"]["chainage"], 12.5)
        self.assertEqual(signals["Rainfall"]["observed"]["source"], "MeasuredRain")


def _config_dict():
    return {
        "schema_version": 1,
        "project": {"name": "Test", "output_folder": "output"},
        "result_type": "network",
        "results": [{"name": "event", "path": "network.res1d"}],
        "monitoring_sources": {
            "all": {"format": "dfs0", "path": "all.dfs0"},
            "gates": {"format": "dfs0", "path": "gates.dfs0"},
        },
        "gauges": [{
            "alias": "Gate Chamber",
            "signals": [{
                "alias": "Anything User Wants",
                "observed": {"source": "gates", "column": "Gate Position"},
                "simulated": {
                    "element_type": "gate",
                    "muid": "Gate1",
                    "quantity": "GateLevel",
                },
            }],
            "events": [{
                "name": "event",
                "start": "2024-01-01",
                "end": "2024-01-02",
            }],
        }],
    }


if __name__ == "__main__":
    unittest.main()
