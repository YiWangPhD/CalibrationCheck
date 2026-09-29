"""Translate calibration signals to the res1d2excel programmatic API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .models import CalibrationConfig, Signal, SimulatedElement


SignalKey = tuple[str, str]


@dataclass(frozen=True)
class ExtractionPlan:
    spec: dict[str, Any]
    aliases: dict[SignalKey, str]


class ModelData:
    """Gauge/signal-oriented view over res1d2excel extraction results."""

    def __init__(self, results: Any, aliases: dict[SignalKey, str]) -> None:
        self._results = results
        self._aliases = aliases

    def has_signal(self, gauge_alias: str, signal_alias: str) -> bool:
        return (gauge_alias, signal_alias) in self._aliases

    def get_series(
            self,
            gauge_alias: str,
            signal_alias: str,
            result_name: str,
            ) -> pd.Series:
        alias = self._aliases[(gauge_alias, signal_alias)]
        return self._results.get_series(
            alias,
            result_name=result_name,
            result_type="network",
        )

    def get_metadata(self, gauge_alias: str, signal_alias: str) -> dict[str, Any]:
        alias = self._aliases[(gauge_alias, signal_alias)]
        return self._results.get_metadata(alias, result_type="network")


def build_res1d2excel_spec(config: CalibrationConfig) -> ExtractionPlan:
    """Build an in-memory res1d2excel specification from simulated signals."""
    spec: dict[str, Any] = {
        element_type: []
        for element_type in [
            "node", "link", "orifice", "pump", "regulation", "weir",
            "valve", "bridge", "culvert", "direct_discharge", "gate",
        ]
    }
    spec["combined"] = []
    spec["res1d_files"] = [
        {
            "result_type": "network",
            "short_name": result.name,
            "res1d_file_path": str(result.path),
            "start_time": _timestamp_text(result.start_time),
            "end_time": _timestamp_text(result.end_time),
        }
        for result in config.results
    ]
    spec["output_files"] = {
        "output_folder": str(config.project.output_folder),
        "to_html": False,
        "export_by_element": False,
        "export_by_result_file": False,
        "export_statistics": False,
    }

    aliases: dict[SignalKey, str] = {}
    for gauge in config.gauges:
        for signal in gauge.signals:
            if signal.simulated is None:
                continue
            result_alias = _result_alias(gauge.alias, signal.alias)
            aliases[(gauge.alias, signal.alias)] = result_alias
            if signal.simulated.element is not None:
                _append_element(spec, result_alias, signal.simulated.element)
                continue
            terms = []
            for index, term in enumerate(signal.simulated.terms, start=1):
                component_alias = f"{result_alias} :: component {index}"
                _append_element(spec, component_alias, term.element)
                terms.append({
                    "op": term.operation,
                    "source": term.element.element_type,
                    "alias": component_alias,
                })
            spec["combined"].append({
                "alias": result_alias,
                "quantity": "CalculatedDischarge",
                "terms": terms,
            })
    return ExtractionPlan(spec, aliases)


def extract_simulated(config: CalibrationConfig) -> ModelData:
    """Extract all configured simulated signals without file-export side effects."""
    try:
        from res1d2excel import extract_from_spec
    except ImportError as exc:
        raise RuntimeError(
            "res1d2excel is required to extract simulated data."
        ) from exc
    plan = build_res1d2excel_spec(config)
    results = extract_from_spec(plan.spec)
    return ModelData(results, plan.aliases)


def _append_element(
        spec: dict[str, Any],
        alias: str,
        element: SimulatedElement,
        ) -> None:
    row: dict[str, Any] = {
        "alias": alias,
        "quantity": element.quantity,
        "muid": element.muid,
    }
    if element.chainage is not None:
        row["chainage"] = element.chainage
    spec[element.element_type].append(row)


def _result_alias(gauge_alias: str, signal_alias: str) -> str:
    return f"{gauge_alias} :: {signal_alias}"


def _timestamp_text(value: pd.Timestamp | None) -> str | None:
    return value.isoformat() if value is not None else None
