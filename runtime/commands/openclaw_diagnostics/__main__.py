from __future__ import annotations

import argparse
from pathlib import Path

from openclaw_diagnostics.cgroup import cgroup_limits_lines
from openclaw_diagnostics.config_summary import summarize_config
from openclaw_diagnostics.config_validation import run_validate_config
from openclaw_diagnostics.diagnostic_artifacts import list_diagnostic_artifact_lines
from openclaw_diagnostics.node_report import summarize_reports
from openclaw_diagnostics.probe import monitor_readiness, probe_readiness, probe_readyz
from openclaw_diagnostics.process_monitor import monitor_memory
from openclaw_diagnostics.session_monitor import monitor_session_logs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openclaw_diagnostics")
    sub = parser.add_subparsers(dest="command", required=True)

    p_summary = sub.add_parser("config-summary", help="Print channels/plugins summary from openclaw.json")
    p_summary.add_argument("config_path", type=Path)

    p_validate = sub.add_parser("validate-config", help="Validate openclaw.json has token auth configured")
    p_validate.add_argument("config_path", type=Path)

    sub.add_parser("probe-readiness", help="HTTP GET /ready once")
    sub.add_parser("probe-readyz", help="HTTP GET /readyz once")

    p_mr = sub.add_parser("monitor-readiness", help="Poll /ready until ready or attempts exhausted")
    p_mr.add_argument("--pid", type=int, required=True)
    p_mr.add_argument("--attempts", type=int, default=48)
    p_mr.add_argument("--interval", type=int, default=5)

    p_mm = sub.add_parser("monitor-memory", help="Periodic /proc stats for gateway PID")
    p_mm.add_argument("--pid", type=int, required=True)
    p_mm.add_argument("--interval", type=int, default=10)
    p_mm.add_argument(
        "--max-samples",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N snapshots (default: run until the process exits)",
    )

    sub.add_parser("cgroup-limits", help="Print cgroup memory limits at startup")

    p_art = sub.add_parser("diagnostic-artifacts", help="List files in Node diagnostic directory")
    p_art.add_argument("diagnostic_dir", type=Path)

    p_nr = sub.add_parser("node-report", help="Summarize Node diagnostic JSON reports")
    p_nr.add_argument("diagnostic_dir", type=Path)

    p_ms = sub.add_parser("monitor-sessions", help="Mirror agent session JSONL events into stdout")
    p_ms.add_argument("--state-dir", type=Path, required=True)
    p_ms.add_argument("--interval", type=float, default=1.0)
    p_ms.add_argument(
        "--max-scans",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N scans (default: run forever)",
    )

    args = parser.parse_args(argv)

    if args.command == "validate-config":
        return run_validate_config(args.config_path)

    if args.command == "config-summary":
        for line in summarize_config(args.config_path):
            print(line)
        return 0

    if args.command == "probe-readiness":
        probe_readiness()
        return 0

    if args.command == "probe-readyz":
        probe_readyz()
        return 0

    if args.command == "monitor-readiness":
        monitor_readiness(args.pid, attempts=args.attempts, interval_seconds=args.interval)
        return 0

    if args.command == "monitor-memory":
        max_samples = args.max_samples if args.max_samples and args.max_samples > 0 else None
        monitor_memory(args.pid, interval_seconds=args.interval, max_samples=max_samples)
        return 0

    if args.command == "cgroup-limits":
        for line in cgroup_limits_lines():
            print(line)
        return 0

    if args.command == "diagnostic-artifacts":
        for line in list_diagnostic_artifact_lines(args.diagnostic_dir):
            print(line)
        return 0

    if args.command == "node-report":
        if not args.diagnostic_dir.is_dir():
            return 0
        for line in summarize_reports(args.diagnostic_dir):
            print(line)
        return 0

    if args.command == "monitor-sessions":
        max_scans = args.max_scans if args.max_scans and args.max_scans > 0 else None
        monitor_session_logs(
            state_dir=args.state_dir,
            interval_seconds=args.interval,
            max_scans=max_scans,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
