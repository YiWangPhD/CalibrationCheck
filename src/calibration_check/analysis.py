"""Event slicing and generic observed-versus-simulated comparisons."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .extraction import ModelData
from .models import CalibrationConfig, Event, Gauge, ResultDefinition, Signal
from .monitoring import MonitoringRepository


@dataclass(frozen=True)
class SignalComparison:
    gauge_alias: str
    event_name: str
    result_name: str
    signal_alias: str
    observed: pd.Series | None
    simulated: pd.Series | None
    metrics: dict[str, float]
    warnings: tuple[str, ...] = ()


@dataclass
class CalibrationRun:
    config: CalibrationConfig
    comparisons: list[SignalComparison] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def analyze(
        config: CalibrationConfig,
        monitoring: MonitoringRepository,
        model_data: ModelData,
        ) -> CalibrationRun:
    """Analyze every configured gauge event against overlapping results."""
    run = CalibrationRun(config)
    for gauge in config.gauges:
        for event in gauge.events:
            matching_results = [
                result for result in config.results
                if _result_overlaps_event(result, event)
            ]
            if not matching_results:
                run.warnings.append(
                    f"No result overlaps event '{gauge.alias}/{event.name}'."
                )
                continue
            for result in matching_results:
                for signal in gauge.signals:
                    comparison = _compare_signal(
                        gauge, event, result, signal, monitoring, model_data
                    )
                    run.comparisons.append(comparison)
                    run.warnings.extend(comparison.warnings)
    return run


def calculate_metrics(
        observed: pd.Series | None,
        simulated: pd.Series | None,
        ) -> dict[str, float]:
    """Calculate generic metrics without assuming a signal category."""
    metrics: dict[str, float] = {}
    if observed is not None and not observed.dropna().empty:
        metrics["observed_max"] = float(observed.max())
        metrics["observed_mean"] = float(observed.mean())
        metrics["observed_integral"] = _integral(observed)
    if simulated is not None and not simulated.dropna().empty:
        metrics["simulated_max"] = float(simulated.max())
        metrics["simulated_mean"] = float(simulated.mean())
        metrics["simulated_integral"] = _integral(simulated)
    if observed is None or simulated is None:
        return metrics

    aligned = _align_series(observed, simulated)
    if aligned.empty:
        return metrics
    error = aligned["simulated"] - aligned["observed"]
    metrics.update({
        "paired_count": float(len(aligned)),
        "mean_error": float(error.mean()),
        "mae": float(error.abs().mean()),
        "rmse": float(np.sqrt(np.mean(np.square(error)))),
        "peak_difference": float(
            aligned["simulated"].max() - aligned["observed"].max()
        ),
    })
    observed_integral = _integral(aligned["observed"])
    simulated_integral = _integral(aligned["simulated"])
    metrics["integral_difference"] = simulated_integral - observed_integral
    if observed_integral != 0:
        metrics["integral_difference_percent"] = (
            (simulated_integral - observed_integral) / observed_integral * 100
        )
    if len(aligned) > 1:
        correlation = aligned["observed"].corr(aligned["simulated"])
        if pd.notna(correlation):
            metrics["correlation"] = float(correlation)
    return metrics


def _compare_signal(
        gauge: Gauge,
        event: Event,
        result: ResultDefinition,
        signal: Signal,
        monitoring: MonitoringRepository,
        model_data: ModelData,
        ) -> SignalComparison:
    warnings: list[str] = []
    observed = None
    simulated = None
    if signal.observed is not None:
        try:
            observed = _slice(
                monitoring.get_series(signal.observed), event.start, event.end
            )
            if observed.empty:
                warnings.append(
                    f"No observed data for '{gauge.alias}/{signal.alias}' "
                    f"during event '{event.name}'."
                )
        except Exception as exc:
            warnings.append(
                f"Observed data failed for '{gauge.alias}/{signal.alias}': {exc}"
            )
    if signal.simulated is not None:
        try:
            simulated = _slice(
                model_data.get_series(gauge.alias, signal.alias, result.name),
                event.start,
                event.end,
            )
            if simulated.empty:
                warnings.append(
                    f"No simulated data for '{gauge.alias}/{signal.alias}' "
                    f"during event '{event.name}' in '{result.name}'."
                )
        except Exception as exc:
            warnings.append(
                f"Simulated data failed for '{gauge.alias}/{signal.alias}' "
                f"in '{result.name}': {exc}"
            )
    metrics = calculate_metrics(observed, simulated)
    return SignalComparison(
        gauge.alias,
        event.name,
        result.name,
        signal.alias,
        observed,
        simulated,
        metrics,
        tuple(warnings),
    )


def _result_overlaps_event(result: ResultDefinition, event: Event) -> bool:
    start = result.start_time or pd.Timestamp.min
    end = result.end_time or pd.Timestamp.max
    return start <= event.end and end >= event.start


def _slice(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    if not isinstance(series.index, pd.DatetimeIndex):
        raise ValueError("Time series requires a DatetimeIndex.")
    return series.sort_index().loc[start:end].copy()


def _align_series(observed: pd.Series, simulated: pd.Series) -> pd.DataFrame:
    observed = _deduplicate_index(observed.dropna().sort_index())
    simulated = _deduplicate_index(simulated.dropna().sort_index())
    if observed.empty or simulated.empty:
        return pd.DataFrame(columns=["observed", "simulated"])
    common_start = max(observed.index.min(), simulated.index.min())
    common_end = min(observed.index.max(), simulated.index.max())
    if common_start > common_end:
        return pd.DataFrame(columns=["observed", "simulated"])
    target = simulated.loc[common_start:common_end].index
    if target.empty:
        return pd.DataFrame(columns=["observed", "simulated"])
    union = observed.index.union(target)
    observed_aligned = (
        observed.reindex(union)
        .interpolate(method="time", limit_area="inside")
        .reindex(target)
    )
    return pd.DataFrame({
        "observed": observed_aligned,
        "simulated": simulated.reindex(target),
    }).dropna()


def _deduplicate_index(series: pd.Series) -> pd.Series:
    if not series.index.has_duplicates:
        return series
    return series.groupby(level=0).mean()


def _integral(series: pd.Series) -> float:
    clean = series.dropna().sort_index()
    if len(clean) < 2 or not isinstance(clean.index, pd.DatetimeIndex):
        return float("nan")
    seconds = (clean.index - clean.index[0]).total_seconds().to_numpy()
    return float(np.trapezoid(clean.to_numpy(dtype=float), seconds))
