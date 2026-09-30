"""End-to-end calibration workflow."""

from __future__ import annotations

from pathlib import Path

from .analysis import CalibrationRun, analyze
from .config import load_config
from .extraction import extract_simulated
from .models import MonitoringPickleOptions
from .monitoring import MonitoringRepository
from .report import export_html


def run_calibration(
        config_path: str | Path,
        monitoring_pickle: MonitoringPickleOptions | None = None,
        ) -> tuple[CalibrationRun, Path]:
    """Load, extract, compare, and export a static HTML report."""
    config = load_config(config_path)
    monitoring = MonitoringRepository(config, pickle_options=monitoring_pickle)
    model_data = extract_simulated(config)
    run = analyze(config, monitoring, model_data)
    run.warnings.extend(monitoring.warnings)
    return run, export_html(run)
