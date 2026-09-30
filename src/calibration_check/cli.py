"""Command-line interface."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import convert_network_ini, load_config
from .models import MonitoringPickleOptions
from .pipeline import run_calibration


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="calibration-check",
        description="Compare DFS0 monitoring data with MIKE 1D network results.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a calibration report")
    run_parser.add_argument("config", help="JSON or legacy network INI file")
    run_parser.add_argument(
        "--use-monitoring-pickle",
        metavar="PATH",
        help="Use raw observed monitoring DataFrames from a trusted pickle file.",
    )
    run_parser.add_argument(
        "--create-monitoring-pickle",
        metavar="PATH",
        help="Create or update a raw observed monitoring pickle from DFS0 files.",
    )

    validate_parser = subparsers.add_parser(
        "validate", help="Validate a JSON or legacy network INI file"
    )
    validate_parser.add_argument("config")

    convert_parser = subparsers.add_parser(
        "convert-network-ini",
        help="Convert a legacy network INI to canonical JSON",
    )
    convert_parser.add_argument("ini")
    convert_parser.add_argument("--output", "-o", required=True)

    args = parser.parse_args(argv)
    if args.command == "run":
        pickle_options = _pickle_options_from_args(args, parser)
        run, output = run_calibration(args.config, pickle_options)
        print(f"Report written to: {output}")
        if run.warnings:
            print(f"Completed with {len(run.warnings)} warning(s).")
            for warning in run.warnings:
                print(f"Warning: {warning}")
        return
    if args.command == "validate":
        config = load_config(args.config)
        print(
            f"Valid: {len(config.gauges)} gauge(s), "
            f"{len(config.results)} result file(s)."
        )
        return
    conversion = convert_network_ini(args.ini)
    output = conversion.write_json(Path(args.output))
    print(f"Converted configuration written to: {output}")
    for warning in conversion.warnings:
        print(f"Warning: {warning}")


def _pickle_options_from_args(
        args: argparse.Namespace,
        parser: argparse.ArgumentParser,
        ) -> MonitoringPickleOptions | None:
    use_path = args.use_monitoring_pickle
    create_path = args.create_monitoring_pickle
    if use_path is None and create_path is None:
        return None
    if (
        use_path is not None
        and create_path is not None
        and Path(use_path) != Path(create_path)
    ):
        parser.error(
            "--use-monitoring-pickle and --create-monitoring-pickle must use "
            "the same path when both are provided."
        )
    return MonitoringPickleOptions(
        use=use_path is not None,
        create_or_update=create_path is not None,
        path=Path(create_path or use_path).expanduser().resolve(),
    )
