"""Canonical configuration and runtime data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


NETWORK_ELEMENT_TYPES = {
    "node", "link", "orifice", "pump", "regulation", "weir", "valve",
    "bridge", "culvert", "direct_discharge", "gate",
}

COMBINABLE_QUANTITIES = {
    "Discharge", "DischargeInStructure", "OverlandFlow", "InterFlow",
    "BaseFlow", "LowerBaseFlow", "InfiltrationToGroundWater",
    "TotalInflow", "TotalOutflow", "TotalRunOff", "Flow",
}


class ConfigurationError(ValueError):
    """Raised when a calibration configuration is invalid."""

    def __init__(self, errors: list[str] | str) -> None:
        self.errors = [errors] if isinstance(errors, str) else errors
        super().__init__("\n".join(self.errors))


@dataclass(frozen=True)
class ProjectOptions:
    name: str = "Calibration Check"
    output_folder: Path = Path("output")


@dataclass(frozen=True)
class ReportOptions:
    title: str = "Calibration Check"
    output_file: str = "calibration_report.html"
    self_contained: bool = True


@dataclass(frozen=True)
class MonitoringSource:
    alias: str
    format: str
    path: Path


@dataclass(frozen=True)
class MonitoringPickleOptions:
    use: bool = False
    path: Path | None = None
    create_or_update: bool = False


@dataclass(frozen=True)
class ObservedReference:
    source: str
    column: str
    scale: float = 1.0
    offset: float = 0.0


@dataclass(frozen=True)
class SimulatedElement:
    element_type: str
    muid: str
    quantity: str
    chainage: float | None = None


@dataclass(frozen=True)
class SimulatedTerm:
    operation: str
    element: SimulatedElement


@dataclass(frozen=True)
class SimulatedReference:
    element: SimulatedElement | None = None
    terms: tuple[SimulatedTerm, ...] = ()


@dataclass(frozen=True)
class Signal:
    alias: str
    observed: ObservedReference | None = None
    simulated: SimulatedReference | None = None


@dataclass(frozen=True)
class Event:
    name: str
    start: pd.Timestamp
    end: pd.Timestamp


@dataclass(frozen=True)
class Gauge:
    alias: str
    signals: tuple[Signal, ...]
    events: tuple[Event, ...]


@dataclass(frozen=True)
class ResultDefinition:
    name: str
    path: Path
    start_time: pd.Timestamp | None = None
    end_time: pd.Timestamp | None = None


@dataclass(frozen=True)
class CalibrationConfig:
    schema_version: int
    project: ProjectOptions
    result_type: str
    results: tuple[ResultDefinition, ...]
    monitoring_sources: dict[str, MonitoringSource]
    gauges: tuple[Gauge, ...]
    monitoring_pickle: MonitoringPickleOptions = field(
        default_factory=MonitoringPickleOptions
    )
    report: ReportOptions = field(default_factory=ReportOptions)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "schema_version": self.schema_version,
            "project": {
                "name": self.project.name,
                "output_folder": str(self.project.output_folder),
            },
            "result_type": self.result_type,
            "results": [{
                "name": result.name,
                "path": str(result.path),
                "start_time": _timestamp_text(result.start_time),
                "end_time": _timestamp_text(result.end_time),
            } for result in self.results],
            "monitoring_sources": {
                alias: {"format": source.format, "path": str(source.path)}
                for alias, source in self.monitoring_sources.items()
            },
            "monitoring_pickle": {
                "use": self.monitoring_pickle.use,
                "path": (
                    str(self.monitoring_pickle.path)
                    if self.monitoring_pickle.path is not None else ""
                ),
                "create_or_update": self.monitoring_pickle.create_or_update,
            },
            "gauges": [{
                "alias": gauge.alias,
                "signals": [_signal_to_dict(signal) for signal in gauge.signals],
                "events": [{
                    "name": event.name,
                    "start": event.start.isoformat(),
                    "end": event.end.isoformat(),
                } for event in gauge.events],
            } for gauge in self.gauges],
            "report": {
                "title": self.report.title,
                "output_file": self.report.output_file,
                "self_contained": self.report.self_contained,
            },
        }


def parse_config(data: dict[str, Any], base_dir: Path) -> CalibrationConfig:
    """Parse and validate the canonical JSON-shaped configuration."""
    errors: list[str] = []
    schema_version = data.get("schema_version", 1)
    if schema_version != 1:
        errors.append(f"Unsupported schema_version: {schema_version}")

    result_type = str(data.get("result_type", "network")).lower()
    if result_type != "network":
        errors.append("Only result_type 'network' is currently supported.")

    project_data = data.get("project") or {}
    project = ProjectOptions(
        name=str(project_data.get("name") or "Calibration Check"),
        output_folder=_resolve_path(
            project_data.get("output_folder", "output"), base_dir
        ),
    )
    report_data = data.get("report") or {}
    report = ReportOptions(
        title=str(report_data.get("title") or project.name),
        output_file=str(
            report_data.get("output_file") or "calibration_report.html"
        ),
        self_contained=_parse_bool(report_data.get("self_contained", True)),
    )

    sources: dict[str, MonitoringSource] = {}
    for alias, value in (data.get("monitoring_sources") or {}).items():
        if not isinstance(value, dict):
            errors.append(f"Monitoring source '{alias}' must be an object.")
            continue
        source_format = str(value.get("format") or "").lower()
        if source_format != "dfs0":
            errors.append(
                f"Monitoring source '{alias}' has unsupported format "
                f"'{source_format}'. Only dfs0 is currently supported."
            )
        path = value.get("path")
        if not path:
            errors.append(f"Monitoring source '{alias}' is missing path.")
            continue
        sources[str(alias)] = MonitoringSource(
            str(alias), source_format, _resolve_path(path, base_dir)
        )

    pickle_data = data.get("monitoring_pickle") or {}
    if not isinstance(pickle_data, dict):
        errors.append("monitoring_pickle must be an object.")
        pickle_data = {}
    pickle_path_value = pickle_data.get("path")
    monitoring_pickle = MonitoringPickleOptions(
        use=_parse_bool(pickle_data.get("use", False)),
        path=(
            _resolve_path(pickle_path_value, base_dir)
            if pickle_path_value not in (None, "") else None
        ),
        create_or_update=_parse_bool(
            pickle_data.get("create_or_update", False)
        ),
    )
    if (monitoring_pickle.use or monitoring_pickle.create_or_update) and (
        monitoring_pickle.path is None
    ):
        errors.append(
            "monitoring_pickle.path is required when using or creating a "
            "monitoring pickle."
        )

    results: list[ResultDefinition] = []
    result_names: set[str] = set()
    for index, value in enumerate(data.get("results") or [], start=1):
        if not isinstance(value, dict):
            errors.append(f"Result {index} must be an object.")
            continue
        name = str(value.get("name") or "").strip()
        path = value.get("path")
        if not name or not path:
            errors.append(f"Result {index} requires name and path.")
            continue
        if name in result_names:
            errors.append(f"Duplicate result name: {name}")
        result_names.add(name)
        start = _parse_timestamp(value.get("start_time"), f"result {name}", errors)
        end = _parse_timestamp(value.get("end_time"), f"result {name}", errors)
        if start is not None and end is not None and start > end:
            errors.append(f"Result '{name}' start_time is after end_time.")
        results.append(ResultDefinition(name, _resolve_path(path, base_dir), start, end))
    if not results:
        errors.append("At least one network result is required.")

    gauges: list[Gauge] = []
    gauge_aliases: set[str] = set()
    for gauge_index, value in enumerate(data.get("gauges") or [], start=1):
        if not isinstance(value, dict):
            errors.append(f"Gauge {gauge_index} must be an object.")
            continue
        alias = str(value.get("alias") or "").strip()
        if not alias:
            errors.append(f"Gauge {gauge_index} is missing alias.")
            continue
        if alias in gauge_aliases:
            errors.append(f"Duplicate gauge alias: {alias}")
        gauge_aliases.add(alias)
        signals = _parse_signals(alias, value.get("signals"), sources, errors)
        events = _parse_events(alias, value.get("events"), errors)
        gauges.append(Gauge(alias, tuple(signals), tuple(events)))
    if not gauges:
        errors.append("At least one gauge is required.")

    if errors:
        raise ConfigurationError(errors)
    return CalibrationConfig(
        schema_version=1,
        project=project,
        result_type=result_type,
        results=tuple(results),
        monitoring_sources=sources,
        gauges=tuple(gauges),
        monitoring_pickle=monitoring_pickle,
        report=report,
    )


def _parse_signals(
        gauge_alias: str,
        values: Any,
        sources: dict[str, MonitoringSource],
        errors: list[str],
        ) -> list[Signal]:
    if not isinstance(values, list) or not values:
        errors.append(f"Gauge '{gauge_alias}' requires at least one signal.")
        return []
    signals: list[Signal] = []
    aliases: set[str] = set()
    for index, value in enumerate(values, start=1):
        if not isinstance(value, dict):
            errors.append(f"Gauge '{gauge_alias}' signal {index} must be an object.")
            continue
        alias = str(value.get("alias") or "").strip()
        if not alias:
            errors.append(f"Gauge '{gauge_alias}' signal {index} is missing alias.")
            continue
        if alias in aliases:
            errors.append(f"Gauge '{gauge_alias}' has duplicate signal alias '{alias}'.")
        aliases.add(alias)
        observed = _parse_observed(gauge_alias, alias, value.get("observed"), sources, errors)
        simulated = _parse_simulated(gauge_alias, alias, value.get("simulated"), errors)
        if observed is None and simulated is None:
            errors.append(
                f"Gauge '{gauge_alias}' signal '{alias}' requires observed "
                "or simulated data."
            )
        signals.append(Signal(alias, observed, simulated))
    return signals


def _parse_observed(
        gauge: str,
        signal: str,
        value: Any,
        sources: dict[str, MonitoringSource],
        errors: list[str],
        ) -> ObservedReference | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append(f"Observed definition for '{gauge}/{signal}' must be an object.")
        return None
    source = str(value.get("source") or "").strip()
    column = str(value.get("column") or "").strip()
    if not source or not column:
        errors.append(f"Observed definition for '{gauge}/{signal}' requires source and column.")
        return None
    if source not in sources:
        errors.append(f"Observed definition for '{gauge}/{signal}' references unknown source '{source}'.")
    try:
        scale = float(value.get("scale", 1.0))
        offset = float(value.get("offset", 0.0))
    except (TypeError, ValueError):
        errors.append(f"Observed scale and offset for '{gauge}/{signal}' must be numeric.")
        scale, offset = 1.0, 0.0
    return ObservedReference(source, column, scale, offset)


def _parse_simulated(
        gauge: str,
        signal: str,
        value: Any,
        errors: list[str],
        ) -> SimulatedReference | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append(f"Simulated definition for '{gauge}/{signal}' must be an object.")
        return None
    if "terms" in value:
        raw_terms = value.get("terms")
        if not isinstance(raw_terms, list) or not raw_terms:
            errors.append(f"Combined signal '{gauge}/{signal}' requires terms.")
            return None
        terms: list[SimulatedTerm] = []
        for index, raw in enumerate(raw_terms, start=1):
            operation = str(raw.get("operation", "+")) if isinstance(raw, dict) else "+"
            if operation not in {"+", "-"}:
                errors.append(f"Term {index} for '{gauge}/{signal}' has invalid operation '{operation}'.")
            element = _parse_element(raw, f"{gauge}/{signal} term {index}", errors)
            if element is not None:
                if element.quantity not in COMBINABLE_QUANTITIES:
                    errors.append(
                        f"Combined term {index} for '{gauge}/{signal}' uses "
                        f"unsupported quantity '{element.quantity}'."
                    )
                terms.append(SimulatedTerm(operation, element))
        return SimulatedReference(terms=tuple(terms))
    element = _parse_element(value, f"{gauge}/{signal}", errors)
    return SimulatedReference(element=element) if element is not None else None


def _parse_element(value: Any, label: str, errors: list[str]) -> SimulatedElement | None:
    if not isinstance(value, dict):
        errors.append(f"Simulated element '{label}' must be an object.")
        return None
    element_type = str(value.get("element_type") or "").lower()
    muid = str(value.get("muid") or "").strip()
    quantity = str(value.get("quantity") or "").strip()
    if element_type not in NETWORK_ELEMENT_TYPES:
        errors.append(f"Simulated element '{label}' has invalid element_type '{element_type}'.")
    if not muid or not quantity:
        errors.append(f"Simulated element '{label}' requires muid and quantity.")
    chainage = value.get("chainage")
    if chainage not in (None, ""):
        try:
            chainage = float(chainage)
        except (TypeError, ValueError):
            errors.append(f"Simulated element '{label}' has invalid chainage '{chainage}'.")
            chainage = None
    else:
        chainage = None
    if element_type not in NETWORK_ELEMENT_TYPES or not muid or not quantity:
        return None
    return SimulatedElement(element_type, muid, quantity, chainage)


def _parse_events(gauge: str, values: Any, errors: list[str]) -> list[Event]:
    if not isinstance(values, list) or not values:
        errors.append(f"Gauge '{gauge}' requires at least one event.")
        return []
    events: list[Event] = []
    names: set[str] = set()
    for index, value in enumerate(values, start=1):
        if not isinstance(value, dict):
            errors.append(f"Gauge '{gauge}' event {index} must be an object.")
            continue
        name = str(value.get("name") or f"event_{index}").strip()
        if name in names:
            errors.append(f"Gauge '{gauge}' has duplicate event name '{name}'.")
        names.add(name)
        start = _parse_timestamp(value.get("start"), f"{gauge}/{name}", errors)
        end = _parse_timestamp(value.get("end"), f"{gauge}/{name}", errors)
        if start is None or end is None:
            continue
        if start >= end:
            errors.append(f"Event '{gauge}/{name}' start must be before end.")
        events.append(Event(name, start, end))
    return events


def _resolve_path(value: str | Path, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


def _parse_timestamp(value: Any, label: str, errors: list[str]) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        errors.append(f"Invalid timestamp for '{label}': {value}")
        return None


def _parse_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "on"}
    return bool(value)


def _timestamp_text(value: pd.Timestamp | None) -> str | None:
    return value.isoformat() if value is not None else None


def _signal_to_dict(signal: Signal) -> dict[str, Any]:
    data: dict[str, Any] = {"alias": signal.alias}
    if signal.observed is not None:
        data["observed"] = {
            "source": signal.observed.source,
            "column": signal.observed.column,
        }
        if signal.observed.scale != 1.0:
            data["observed"]["scale"] = signal.observed.scale
        if signal.observed.offset != 0.0:
            data["observed"]["offset"] = signal.observed.offset
    if signal.simulated is not None:
        if signal.simulated.element is not None:
            data["simulated"] = _element_to_dict(signal.simulated.element)
        else:
            data["simulated"] = {
                "terms": [{
                    "operation": term.operation,
                    **_element_to_dict(term.element),
                } for term in signal.simulated.terms]
            }
    return data


def _element_to_dict(element: SimulatedElement) -> dict[str, Any]:
    data: dict[str, Any] = {
        "element_type": element.element_type,
        "muid": element.muid,
        "quantity": element.quantity,
    }
    if element.chainage is not None:
        data["chainage"] = element.chainage
    return data
