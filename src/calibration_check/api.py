"""Programmatic calibration evaluation API."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from .analysis import SignalComparison, _align_series, analyze
from .config import load_config
from .extraction import extract_simulated
from .models import CalibrationConfig, MonitoringPickleOptions, parse_config
from .monitoring import MonitoringRepository


StatisticsFunction = Callable[[pd.Series, pd.Series], Mapping[str, float]]
EvaluationRow = dict[str, object]


def evaluate_calibration(
        config: str | Path | dict[str, Any] | CalibrationConfig,
        *,
        statistics: StatisticsFunction | None = None,
        include_builtin_statistics: bool = True,
        monitoring_pickle: MonitoringPickleOptions | None = None,
        ) -> list[EvaluationRow]:
    """Evaluate calibration statistics and return flat rows.

    The returned rows are intended for optimization/calibration drivers that
    need machine-readable statistics without generating reports or receiving
    time series data. Custom statistics receive internally aligned observed and
    simulated series, but those series are not returned.
    """
    parsed_config = _coerce_config(config)
    monitoring = MonitoringRepository(
        parsed_config,
        pickle_options=monitoring_pickle,
    )
    model_data = extract_simulated(parsed_config)
    run = analyze(parsed_config, monitoring, model_data)
    run.warnings.extend(monitoring.warnings)

    rows: list[EvaluationRow] = []
    for comparison in run.comparisons:
        if include_builtin_statistics:
            rows.extend(_metric_rows(comparison, comparison.metrics))
        if statistics is not None:
            rows.extend(_custom_metric_rows(comparison, statistics))
        rows.extend(_warning_rows(comparison))
    comparison_warnings = {
        warning
        for comparison in run.comparisons
        for warning in comparison.warnings
    }
    rows.extend(
        _run_warning_row(warning)
        for warning in run.warnings
        if warning not in comparison_warnings
    )
    return rows


def _coerce_config(
        config: str | Path | dict[str, Any] | CalibrationConfig,
        ) -> CalibrationConfig:
    if isinstance(config, CalibrationConfig):
        return config
    if isinstance(config, (str, Path)):
        return load_config(config)
    if isinstance(config, dict):
        return parse_config(config, Path.cwd())
    raise TypeError(
        "config must be a path, configuration dictionary, or CalibrationConfig."
    )


def _metric_rows(
        comparison: SignalComparison,
        metrics: Mapping[str, float],
        ) -> list[EvaluationRow]:
    return [
        {
            **_comparison_columns(comparison),
            "statistic": name,
            "value": _row_value(value),
            "warning": "",
        }
        for name, value in metrics.items()
    ]


def _custom_metric_rows(
        comparison: SignalComparison,
        statistics: StatisticsFunction,
        ) -> list[EvaluationRow]:
    if comparison.observed is None or comparison.simulated is None:
        return []
    aligned = _align_series(comparison.observed, comparison.simulated)
    if aligned.empty:
        return []
    custom_metrics = statistics(aligned["observed"], aligned["simulated"])
    if not isinstance(custom_metrics, Mapping):
        raise TypeError("Custom statistics function must return a mapping.")
    return _metric_rows(comparison, custom_metrics)


def _warning_rows(comparison: SignalComparison) -> list[EvaluationRow]:
    return [
        {
            **_comparison_columns(comparison),
            "statistic": "",
            "value": None,
            "warning": warning,
        }
        for warning in comparison.warnings
    ]


def _run_warning_row(warning: str) -> EvaluationRow:
    return {
        "gauge": "",
        "signal": "",
        "event": "",
        "result": "",
        "statistic": "",
        "value": None,
        "warning": warning,
    }


def _comparison_columns(comparison: SignalComparison) -> EvaluationRow:
    return {
        "gauge": comparison.gauge_alias,
        "signal": comparison.signal_alias,
        "event": comparison.event_name,
        "result": comparison.result_name,
    }


def _row_value(value: object) -> float | None:
    if pd.isna(value):
        return None
    return float(value)
