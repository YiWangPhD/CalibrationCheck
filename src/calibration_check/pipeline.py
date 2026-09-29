"""End-to-end calibration workflow."""

from __future__ import annotations

from pathlib import Path

from .analysis import CalibrationRun, analyze
from .config import load_config
from .extraction import extract_simulated
from .monitoring import MonitoringRepository
from .report import export_html


def run_calibration(config_path: str | Path) -> tuple[CalibrationRun, Path]:
    """Load, extract, compare, and export a static HTML report."""
    config = load_config(config_path)
    monitoring = MonitoringRepository(config)
    model_data = extract_simulated(config)
    run = analyze(config, monitoring, model_data)
    return run, export_html(run)
