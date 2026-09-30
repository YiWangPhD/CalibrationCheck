"""Monitoring-data access with cached DFS0 sources."""

from __future__ import annotations

import hashlib
import pickle
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .models import CalibrationConfig, MonitoringPickleOptions, MonitoringSource, ObservedReference


class MonitoringDataError(ValueError):
    """Raised when monitoring data cannot be loaded or selected."""


SourceLoader = Callable[[MonitoringSource], pd.DataFrame]


class MonitoringRepository:
    """Load each configured monitoring source once and expose named series."""

    def __init__(
            self,
            config: CalibrationConfig,
            loaders: dict[str, SourceLoader] | None = None,
            pickle_options: MonitoringPickleOptions | None = None,
            ) -> None:
        self._sources = config.monitoring_sources
        self._pickle_options = pickle_options or config.monitoring_pickle
        self._loaders = {"dfs0": load_dfs0}
        if loaders:
            self._loaders.update(loaders)
        self._frames: dict[str, pd.DataFrame] = {}
        self.warnings: list[str] = []
        if self._pickle_options.create_or_update:
            self._create_or_update_pickle()
        elif self._pickle_options.use:
            self._load_pickle()

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

    def _load_pickle(self) -> None:
        path = self._require_pickle_path()
        payload = _read_pickle(path)
        self._frames.update(_frames_from_pickle(payload, self._sources))

    def _create_or_update_pickle(self) -> None:
        path = self._require_pickle_path()
        existing = _read_pickle(path, required=False) if path.exists() else None
        existing_sources = (
            existing.get("sources", {}) if isinstance(existing, dict) else {}
        )
        frames: dict[str, pd.DataFrame] = {}
        payload_sources: dict[str, dict[str, Any]] = {}
        for alias, source in self._sources.items():
            checksum = _file_checksum(source.path)
            cached = existing_sources.get(alias)
            if (
                isinstance(cached, dict)
                and cached.get("checksum") == checksum
                and isinstance(cached.get("data"), pd.DataFrame)
                and isinstance(cached["data"].index, pd.DatetimeIndex)
            ):
                frame = cached["data"].sort_index()
            else:
                loader = self._loaders.get(source.format)
                if loader is None:
                    raise MonitoringDataError(
                        f"No loader is available for monitoring format "
                        f"'{source.format}'."
                    )
                frame = loader(source)
                if not isinstance(frame, pd.DataFrame):
                    raise MonitoringDataError(
                        f"Loader for '{alias}' did not return a DataFrame."
                    )
                if not isinstance(frame.index, pd.DatetimeIndex):
                    raise MonitoringDataError(
                        f"Monitoring source '{alias}' requires a DatetimeIndex."
                    )
                frame = frame.sort_index()
            frames[alias] = frame
            payload_sources[alias] = {
                "format": source.format,
                "filename": source.path.name,
                "checksum": checksum,
                "columns": [str(column) for column in frame.columns],
                "index_type": type(frame.index).__name__,
                "data": frame,
            }
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sources": payload_sources,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        self._frames.update(frames)

    def _require_pickle_path(self) -> Path:
        if self._pickle_options.path is None:
            raise MonitoringDataError("Monitoring pickle path is required.")
        return self._pickle_options.path


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


def _read_pickle(path: Path, required: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise MonitoringDataError(f"Monitoring pickle does not exist: {path}")
        return {}
    try:
        with path.open("rb") as handle:
            payload = pickle.load(handle)
    except Exception as exc:
        raise MonitoringDataError(
            "Could not read monitoring pickle. Only load pickle files you "
            "created or trust."
        ) from exc
    if not isinstance(payload, dict):
        raise MonitoringDataError("Monitoring pickle root must be a dictionary.")
    if payload.get("schema_version") != 1:
        raise MonitoringDataError(
            f"Unsupported monitoring pickle schema_version: "
            f"{payload.get('schema_version')}"
        )
    if not isinstance(payload.get("sources"), dict):
        raise MonitoringDataError("Monitoring pickle is missing sources.")
    return payload


def _frames_from_pickle(
        payload: dict[str, Any],
        sources: dict[str, MonitoringSource],
        ) -> dict[str, pd.DataFrame]:
    payload_sources = payload["sources"]
    frames: dict[str, pd.DataFrame] = {}
    for alias in sources:
        if alias not in payload_sources:
            raise MonitoringDataError(
                f"Monitoring pickle is missing source '{alias}'."
            )
        entry = payload_sources[alias]
        if not isinstance(entry, dict):
            raise MonitoringDataError(
                f"Monitoring pickle source '{alias}' must be an object."
            )
        frame = entry.get("data")
        if not isinstance(frame, pd.DataFrame):
            raise MonitoringDataError(
                f"Monitoring pickle source '{alias}' is missing DataFrame data."
            )
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise MonitoringDataError(
                f"Monitoring pickle source '{alias}' requires a DatetimeIndex."
            )
        frames[alias] = frame.sort_index()
    return frames


def _file_checksum(path: Path) -> str:
    if not path.is_file():
        raise MonitoringDataError(f"Monitoring file does not exist: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
