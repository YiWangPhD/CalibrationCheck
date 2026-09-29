"""Monitoring-data access with cached DFS0 sources."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd

from .models import CalibrationConfig, MonitoringSource, ObservedReference


class MonitoringDataError(ValueError):
    """Raised when monitoring data cannot be loaded or selected."""


SourceLoader = Callable[[MonitoringSource], pd.DataFrame]


class MonitoringRepository:
    """Load each configured monitoring source once and expose named series."""

    def __init__(
            self,
            config: CalibrationConfig,
            loaders: dict[str, SourceLoader] | None = None,
            ) -> None:
        self._sources = config.monitoring_sources
        self._loaders = {"dfs0": load_dfs0}
        if loaders:
            self._loaders.update(loaders)
        self._frames: dict[str, pd.DataFrame] = {}

    def list_columns(self, source_alias: str) -> list[str]:
        return [str(column) for column in self._get_frame(source_alias).columns]

    def get_series(self, reference: ObservedReference) -> pd.Series:
        frame = self._get_frame(reference.source)
        column = _find_column(frame, reference.column)
        series = frame[column].copy()
        series.name = reference.column
        return series * reference.scale + reference.offset

    def _get_frame(self, source_alias: str) -> pd.DataFrame:
        if source_alias in self._frames:
            return self._frames[source_alias]
        if source_alias not in self._sources:
            raise MonitoringDataError(f"Unknown monitoring source: {source_alias}")
        source = self._sources[source_alias]
        loader = self._loaders.get(source.format)
        if loader is None:
            raise MonitoringDataError(
                f"No loader is available for monitoring format '{source.format}'."
            )
        frame = loader(source)
        if not isinstance(frame, pd.DataFrame):
            raise MonitoringDataError(
                f"Loader for '{source_alias}' did not return a DataFrame."
            )
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise MonitoringDataError(
                f"Monitoring source '{source_alias}' requires a DatetimeIndex."
            )
        frame = frame.sort_index()
        self._frames[source_alias] = frame
        return frame


def load_dfs0(source: MonitoringSource) -> pd.DataFrame:
    """Load a DFS0 file as a pandas DataFrame."""
    if not Path(source.path).is_file():
        raise MonitoringDataError(
            f"Monitoring file does not exist for '{source.alias}': {source.path}"
        )
    try:
        import mikeio
    except ImportError as exc:
        raise MonitoringDataError(
            "mikeio is required to read DFS0 monitoring files."
        ) from exc
    try:
        return mikeio.Dfs0(str(source.path)).to_dataframe()
    except Exception as exc:
        raise MonitoringDataError(
            f"Could not read DFS0 source '{source.alias}': {source.path}"
        ) from exc


def _find_column(frame: pd.DataFrame, requested: str):
    if requested in frame.columns:
        return requested
    matches = [column for column in frame.columns if str(column) == requested]
    if not matches:
        available = ", ".join(str(column) for column in frame.columns)
        raise MonitoringDataError(
            f"Monitoring column '{requested}' was not found. "
            f"Available columns: {available or 'none'}"
        )
    if len(matches) > 1:
        raise MonitoringDataError(
            f"Monitoring column name '{requested}' is ambiguous."
        )
    return matches[0]
