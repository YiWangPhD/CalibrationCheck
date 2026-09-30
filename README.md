# CalibrationCheck

`CalibrationCheck` compares DFS0 monitoring data with MIKE 1D network result
files and exports a static Plotly HTML report. It uses `res1d2excel` for model
result extraction and does not require Dash or a running web server.

## Status

Version `0.0.1` supports:

- canonical JSON configuration;
- conversion and direct loading of legacy network INI files;
- any number of aliased DFS0 monitoring sources;
- any number of signals per gauge;
- observed-only, simulated-only, direct, and combined signals;
- event-based comparisons and generic statistics;
- self-contained or CDN-backed static Plotly HTML.

Runoff configuration and CSV monitoring sources are intentionally deferred.

## Commands

```powershell
uv run calibration-check validate calibration.json
uv run calibration-check run calibration.json
uv run calibration-check convert-network-ini old_network.ini -o calibration.json
uv run calibration-check-gui
```

Legacy INI input can also be run directly:

```powershell
uv run calibration-check run old_network.ini
```

## GUI Use

Launch the desktop GUI with:

```powershell
uv run calibration-check-gui
```

The main window loads JSON or legacy INI configurations, validates and runs
reports, shows process output, and offers an `Open Report` button after a
successful run. The configuration editor uses tabs for project settings,
network result files, DFS0 monitoring files, gauges, and report options.

Gauge event tables are populated from the configured result files. Each result
row can be enabled for that gauge, and `skip hours` / `truncate hours` are GUI
helpers only: the JSON stores the resulting event `start` and `end` times.
`Sync Events From Result Files` adds missing result-file events without
overwriting existing event windows.

## JSON Structure

```json
{
  "schema_version": 1,
  "project": {"name": "Network Calibration", "output_folder": "output"},
  "result_type": "network",
  "results": [{
    "name": "event_1",
    "path": "results/event_1.res1d",
    "start_time": "2024-01-01T00:00:00",
    "end_time": "2024-01-03T00:00:00"
  }],
  "monitoring_sources": {
    "station_data": {"format": "dfs0", "path": "monitoring/station.dfs0"}
  },
  "gauges": [{
    "alias": "Pump Station 1",
    "signals": [{
      "alias": "Wet Well Level",
      "observed": {"source": "station_data", "column": "PS1_Level"},
      "simulated": {
        "element_type": "node",
        "muid": "PS1_WetWell",
        "quantity": "WaterLevel"
      }
    }],
    "events": [{
      "name": "WWF_1",
      "start": "2024-01-01T06:00:00",
      "end": "2024-01-02T06:00:00"
    }]
  }],
  "report": {
    "title": "Network Calibration",
    "output_file": "calibration_report.html",
    "self_contained": true
  }
}
```

Paths in JSON are resolved relative to the JSON file. Legacy INI paths retain
the old behavior of resolving input and result filenames under `workfolder`.

For legacy INI conversion, discharge and overflow identifiers are currently
treated as links. Numeric level identifiers are interpreted as link chainages;
string level identifiers are interpreted as node MUIDs.
