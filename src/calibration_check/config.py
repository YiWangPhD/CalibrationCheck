"""JSON loading and legacy network INI conversion."""

from __future__ import annotations

import ast
import configparser
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import CalibrationConfig, ConfigurationError, parse_config


@dataclass(frozen=True)
class ConversionResult:
    """Canonical JSON data produced from a legacy network INI."""

    config: dict[str, Any]
    warnings: tuple[str, ...] = ()

    def write_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.config, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        return target


def load_config(path: str | Path) -> CalibrationConfig:
    """Load JSON or legacy network INI into the canonical configuration."""
    source = Path(path).resolve()
    suffix = source.suffix.lower()
    if suffix == ".json":
        data = json.loads(source.read_text(encoding="utf-8"))
    elif suffix == ".ini":
        data = convert_network_ini(source).config
    else:
        raise ConfigurationError(
            f"Unsupported configuration format '{suffix}'. Use .json or .ini."
        )
    if not isinstance(data, dict):
        raise ConfigurationError("Configuration root must be a JSON object.")
    return parse_config(data, source.parent)


def convert_network_ini(path: str | Path) -> ConversionResult:
    """Convert an old network calibration INI to canonical JSON-shaped data."""
    source = Path(path).resolve()
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    with source.open("r", encoding="utf-8-sig") as handle:
        parser.read_file(handle)

    warnings: list[str] = []
    workfolder_text = _first_value(parser, "FolderWithResultFiles", "workfolder")
    workfolder = Path(workfolder_text) if workfolder_text else source.parent
    if not workfolder.is_absolute():
        workfolder = (source.parent / workfolder).resolve()

    input_section = _section(parser, "inputPickleFiles", required=False)
    monitoring_sources: dict[str, dict[str, str]] = {}
    if input_section is not None:
        for alias, file_path in input_section.items():
            resolved = _join_legacy_path(workfolder, file_path)
            source_format = Path(file_path).suffix.lower().lstrip(".")
            monitoring_sources[alias] = {
                "format": source_format,
                "path": str(resolved),
            }
            if source_format != "dfs0":
                warnings.append(
                    f"Monitoring source '{alias}' uses unsupported format "
                    f"'{source_format or 'unknown'}'."
                )

    rain_source = _find_source_alias(monitoring_sources, "rain")
    flow_source = _find_source_alias(monitoring_sources, "flow")
    level_source = _find_source_alias(monitoring_sources, "level")
    if rain_source is None:
        warnings.append("Could not identify the legacy rainfall monitoring source.")
    if flow_source is None:
        warnings.append("Could not identify the legacy flow monitoring source.")
    if level_source is None:
        warnings.append("Could not identify the legacy level monitoring source.")

    result_section = _section(parser, "ResultFileDictionary")
    results: list[dict[str, Any]] = []
    for file_name, raw_window in result_section.items():
        window = _literal(raw_window, f"ResultFileDictionary.{file_name}")
        result: dict[str, Any] = {
            "name": Path(file_name).stem,
            "path": str(_join_legacy_path(workfolder, file_name)),
        }
        if isinstance(window, (list, tuple)) and len(window) >= 2:
            result["start_time"] = str(window[0])
            result["end_time"] = str(window[1])
        else:
            warnings.append(f"Result '{file_name}' has no valid time window.")
        results.append(result)

    network_section = _section(parser, "NetworkDictionary")
    timestamp_section = _section(parser, "TimeStampDictionary")
    selected_gauges = _selected_gauges(parser, timestamp_section)
    gauges: list[dict[str, Any]] = []
    for gauge_alias in selected_gauges:
        if gauge_alias not in network_section:
            warnings.append(
                f"Gauge '{gauge_alias}' has events but no NetworkDictionary entry."
            )
            continue
        mapping = _literal(
            network_section[gauge_alias],
            f"NetworkDictionary.{gauge_alias}",
        )
        if not isinstance(mapping, (list, tuple)) or len(mapping) < 4:
            warnings.append(
                f"Gauge '{gauge_alias}' has an invalid NetworkDictionary entry."
            )
            continue
        rain_column, level_id, flow_id, overflow_id = mapping[:4]
        signals: list[dict[str, Any]] = []
        if rain_column is not None:
            signals.append(_observed_only_signal(
                "Rainfall", rain_source, str(rain_column), warnings, gauge_alias
            ))
        signals.extend(_legacy_flow_signals(
            gauge_alias, flow_id, flow_source, warnings
        ))
        signals.extend(_legacy_level_signals(
            gauge_alias, level_id, flow_id, level_source, warnings
        ))
        if overflow_id is not None:
            signal: dict[str, Any] = {
                "alias": "CSO Discharge",
                "simulated": {
                    "element_type": "link",
                    "muid": str(overflow_id),
                    "quantity": "Discharge",
                },
            }
            if flow_source is not None:
                signal["observed"] = {
                    "source": flow_source,
                    "column": f"{gauge_alias}_CSO",
                }
            signals.append(signal)

        raw_events = timestamp_section.get(gauge_alias)
        event_values = _literal(
            raw_events, f"TimeStampDictionary.{gauge_alias}"
        ) if raw_events is not None else []
        events = []
        if isinstance(event_values, (list, tuple)):
            for index, window in enumerate(event_values, start=1):
                if isinstance(window, (list, tuple)) and len(window) >= 2:
                    events.append({
                        "name": f"event_{index}",
                        "start": str(window[0]),
                        "end": str(window[1]),
                    })
                else:
                    warnings.append(
                        f"Gauge '{gauge_alias}' event {index} is invalid."
                    )
        if not events:
            warnings.append(f"Gauge '{gauge_alias}' has no valid events.")
        gauges.append({"alias": gauge_alias, "signals": signals, "events": events})

    output_name = _first_value(
        parser, "outputPickleFiles", "outputResList"
    ) or "calibration_report.html"
    output_file = str(Path(output_name).with_suffix(".html").name)
    config = {
        "schema_version": 1,
        "project": {
            "name": source.stem,
            "output_folder": str(source.parent / "output"),
        },
        "result_type": "network",
        "results": results,
        "monitoring_sources": monitoring_sources,
        "gauges": gauges,
        "report": {
            "title": source.stem,
            "output_file": output_file,
            "self_contained": True,
        },
    }
    return ConversionResult(config, tuple(warnings))


def _legacy_flow_signals(
        gauge: str,
        flow_id: Any,
        source: str | None,
        warnings: list[str],
        ) -> list[dict[str, Any]]:
    if flow_id is None:
        return []
    ids = list(flow_id) if isinstance(flow_id, (list, tuple)) else [flow_id]
    if len(ids) == 1:
        simulated: dict[str, Any] = {
            "element_type": "link",
            "muid": str(ids[0]),
            "quantity": "Discharge",
        }
    else:
        simulated = {
            "terms": [{
                "operation": "+",
                "element_type": "link",
                "muid": str(muid),
                "quantity": "Discharge",
            } for muid in ids]
        }
    signal: dict[str, Any] = {"alias": "Flow", "simulated": simulated}
    if source is not None:
        signal["observed"] = {"source": source, "column": gauge}
    else:
        warnings.append(f"Gauge '{gauge}' flow has no monitoring source.")
    return [signal]


def _legacy_level_signals(
        gauge: str,
        level_id: Any,
        flow_id: Any,
        source: str | None,
        warnings: list[str],
        ) -> list[dict[str, Any]]:
    if level_id is None:
        return []
    level_ids = list(level_id) if isinstance(level_id, (list, tuple)) else [level_id]
    signals: list[dict[str, Any]] = []
    for index, item in enumerate(level_ids, start=1):
        alias = "Level" if len(level_ids) == 1 else f"Level {index}"
        observed_column = gauge if len(level_ids) == 1 else f"{gauge}_{index}"
        signal: dict[str, Any] = {"alias": alias}
        if source is not None:
            signal["observed"] = {"source": source, "column": observed_column}
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            if isinstance(flow_id, str):
                signal["simulated"] = {
                    "element_type": "link",
                    "muid": flow_id,
                    "quantity": "WaterLevel",
                    "chainage": float(item),
                }
            else:
                warnings.append(
                    f"Gauge '{gauge}' has link level chainage {item} but no "
                    "single flow link MUID."
                )
        elif isinstance(item, str):
            if item == ":":
                if isinstance(flow_id, str):
                    signal["simulated"] = {
                        "element_type": "link",
                        "muid": flow_id,
                        "quantity": "WaterLevel",
                        "chainage": 0.0,
                    }
                    warnings.append(
                        f"Gauge '{gauge}' legacy ':' level was converted to "
                        "chainage 0. Verify this location."
                    )
            else:
                signal["simulated"] = {
                    "element_type": "node",
                    "muid": item,
                    "quantity": "WaterLevel",
                }
        signals.append(signal)
    return signals


def _observed_only_signal(
        alias: str,
        source: str | None,
        column: str,
        warnings: list[str],
        gauge: str,
        ) -> dict[str, Any]:
    signal: dict[str, Any] = {"alias": alias}
    if source is not None:
        signal["observed"] = {"source": source, "column": column}
    else:
        warnings.append(f"Gauge '{gauge}' signal '{alias}' has no monitoring source.")
    return signal


def _selected_gauges(
        parser: configparser.ConfigParser,
        timestamps: configparser.SectionProxy,
        ) -> list[str]:
    raw = _first_value(parser, "CalibrationGauges", "gauges")
    if raw:
        selected = _literal(raw, "CalibrationGauges.gauges")
        if selected:
            return [str(value) for value in selected]
    return list(timestamps.keys())


def _find_source_alias(sources: dict[str, Any], role: str) -> str | None:
    role = role.lower()
    for alias in sources:
        if role in alias.lower():
            return alias
    return None


def _join_legacy_path(folder: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else folder / path


def _literal(value: str, label: str) -> Any:
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError) as exc:
        raise ConfigurationError(f"Invalid legacy value at {label}: {value}") from exc


def _section(
        parser: configparser.ConfigParser,
        name: str,
        required: bool = True,
        ) -> configparser.SectionProxy | None:
    for section_name in parser.sections():
        if section_name.lower() == name.lower():
            return parser[section_name]
    if required:
        raise ConfigurationError(f"Missing legacy INI section: [{name}]")
    return None


def _first_value(
        parser: configparser.ConfigParser,
        section_name: str,
        key: str,
        ) -> str | None:
    section = _section(parser, section_name, required=False)
    if section is None:
        return None
    for actual_key, value in section.items():
        if actual_key.lower() == key.lower():
            return value
    return None
