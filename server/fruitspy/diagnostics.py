from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

SNAPSHOT_FORMAT = "fruitspy-diagnostics-v1"
DEFAULT_METRICS_URL = "http://127.0.0.1:9108/metrics"
_COUNTER_NAMES = frozenset(
    {
        "fruitspy_peerchat_events_total",
        "fruitspy_qr_events_total",
        "fruitspy_discovery_requests_total",
        "fruitspy_discovery_results_total",
        "fruitspy_natneg_session_events_total",
        "fruitspy_natneg_outcomes_total",
        "fruitspy_natneg_reports_total",
        "fruitspy_relay_events_total",
        "fruitspy_relay_packets_total",
        "fruitspy_admission_rejections_total",
        "fruitspy_protocol_rejections_total",
    }
)


def parse_counter_samples(payload: str) -> dict[str, float]:
    samples: dict[str, float] = {}
    for line in payload.splitlines():
        if not line or line.startswith("#"):
            continue
        series, separator, raw_value = line.rpartition(" ")
        if not separator:
            raise ValueError(f"invalid Prometheus sample: {line!r}")
        metric_name = series.split("{", 1)[0]
        if metric_name in _COUNTER_NAMES:
            samples[series] = float(raw_value)
    return samples


def fetch_counter_samples(url: str, timeout: float) -> dict[str, float]:
    with urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    samples = parse_counter_samples(payload)
    if not samples:
        raise ValueError("metrics response contained no diagnostic counters")
    return samples


def write_snapshot(path: Path, samples: dict[str, float]) -> str:
    captured_at = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "format": SNAPSHOT_FORMAT,
        "captured_at": captured_at,
        "metrics": samples,
    }
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return captured_at


def read_snapshot(path: Path) -> dict[str, float]:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if snapshot.get("format") != SNAPSHOT_FORMAT:
        raise ValueError("unsupported diagnostics snapshot format")
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict) or not all(
        isinstance(series, str) and isinstance(value, (int, float))
        for series, value in metrics.items()
    ):
        raise ValueError("invalid diagnostics snapshot metrics")
    return {series: float(value) for series, value in metrics.items()}


def _series(metric: str, label: str, value: str) -> str:
    return f'{metric}{{{label}="{value}"}}'


def _delta(
    before: dict[str, float],
    after: dict[str, float],
    metric: str,
    label: str,
    value: str,
) -> int:
    series = _series(metric, label, value)
    return round(after.get(series, 0.0) - before.get(series, 0.0))


def _metric_delta(
    before: dict[str, float],
    after: dict[str, float],
    metric: str,
) -> int:
    prefix = f"{metric}{{"
    series = set(before) | set(after)
    return round(
        sum(
            after.get(item, 0.0) - before.get(item, 0.0)
            for item in series
            if item.startswith(prefix)
        )
    )

def _changed_metric_series(
    before: dict[str, float],
    after: dict[str, float],
    metric: str,
) -> list[tuple[str, int]]:
    prefix = f"{metric}{{"
    changed: list[tuple[str, int]] = []
    for series in sorted(set(before) | set(after)):
        if not series.startswith(prefix):
            continue
        delta = round(after.get(series, 0.0) - before.get(series, 0.0))
        if delta:
            changed.append((series, delta))
    return changed


def diagnosis_lines(
    before: dict[str, float],
    after: dict[str, float],
    game_result: str = "unknown",
) -> list[str]:
    if game_result not in {"unknown", "passed", "failed"}:
        raise ValueError("game_result must be unknown, passed, or failed")
    reset_series = sorted(
        series
        for series, value in before.items()
        if series in after and after[series] < value
    )
    if reset_series:
        raise ValueError("metrics counters decreased; the FruitSpy process restarted")

    qr = lambda event: _delta(
        before,
        after,
        "fruitspy_qr_events_total",
        "event",
        event,
    )
    peerchat = lambda event: _delta(
        before,
        after,
        "fruitspy_peerchat_events_total",
        "event",
        event,
    )
    discovery = lambda result: _delta(
        before,
        after,
        "fruitspy_discovery_results_total",
        "result",
        result,
    )
    natneg = lambda event: _delta(
        before,
        after,
        "fruitspy_natneg_session_events_total",
        "event",
        event,
    )
    outcome = lambda path: _delta(
        before,
        after,
        "fruitspy_natneg_outcomes_total",
        "outcome",
        path,
    )
    report = lambda result: _delta(
        before,
        after,
        "fruitspy_natneg_reports_total",
        "result",
        result,
    )
    relay = lambda event: _delta(
        before,
        after,
        "fruitspy_relay_events_total",
        "event",
        event,
    )
    packets = lambda result: _delta(
        before,
        after,
        "fruitspy_relay_packets_total",
        "result",
        result,
    )

    accepted = qr("availability_accepted")
    rejected = qr("availability_rejected")
    challenged = qr("challenge_issued")
    registered = qr("registered")
    proof_rejected = qr("proof_rejected")
    chat_registered = peerchat("registered")
    joined = peerchat("joined")
    empty = discovery("empty")
    nonempty = discovery("nonempty")
    discovery_rejected = discovery("rejected")
    created = natneg("created")
    paired = natneg("paired")
    direct = outcome("direct")
    relay_outcome = outcome("relay")
    reports_failed = report("failure")
    relay_activated = relay("activated")
    relay_ready = relay("ready")
    relay_unavailable = relay("unavailable")
    relay_established = relay("established")
    forwarded = packets("forwarded")
    dropped = packets("dropped")
    admission_rejections = _metric_delta(
        before,
        after,
        "fruitspy_admission_rejections_total",
    )
    protocol_rejections = _metric_delta(
        before,
        after,
        "fruitspy_protocol_rejections_total",
    )

    lines: list[str] = []
    if accepted:
        lines.append(
            f"PASS stage=availability accepted={accepted} rejected={rejected}"
        )
    elif rejected:
        lines.append(f"FAIL stage=availability accepted=0 rejected={rejected}")
    else:
        lines.append("MISSING stage=availability detail=no_accepted_request")

    if registered:
        lines.append(
            f"PASS stage=qr_registration challenges={challenged} registered={registered}"
        )
    elif proof_rejected:
        lines.append(
            "FAIL stage=qr_registration "
            f"challenges={challenged} proof_rejected={proof_rejected}"
        )
    elif challenged:
        lines.append(
            f"FAIL stage=qr_registration challenges={challenged} registered=0"
        )
    else:
        lines.append("MISSING stage=qr_registration detail=no_host_heartbeat")

    if chat_registered >= 2 and joined >= 2:
        lines.append(
            f"PASS stage=peerchat registered={chat_registered} joins={joined}"
        )
    elif chat_registered or joined:
        lines.append(
            f"FAIL stage=peerchat registered={chat_registered} joins={joined}"
        )
    else:
        lines.append("MISSING stage=peerchat detail=no_registered_players")

    if nonempty:
        lines.append(
            f"PASS stage=discovery nonempty={nonempty} empty={empty} "
            f"rejected={discovery_rejected}"
        )
    elif discovery_rejected:
        lines.append(
            f"FAIL stage=discovery outcome=game_rejected count={discovery_rejected}"
        )
    elif empty:
        lines.append(f"FAIL stage=discovery nonempty=0 empty={empty}")
    else:
        lines.append("MISSING stage=discovery detail=no_list_query")

    if paired:
        lines.append(f"PASS stage=natneg created={created} paired={paired}")
    elif created:
        lines.append(f"FAIL stage=natneg created={created} paired=0")
    else:
        lines.append("MISSING stage=natneg detail=no_session")

    path = "none"
    if direct:
        path = "direct"
        lines.append(
            f"PASS stage=path outcome=direct established={direct} reports_failed={reports_failed}"
        )
    elif relay_established or relay_outcome:
        path = "relay"
        status = (
            "PASS"
            if forwarded and not dropped
            else "WARN"
            if forwarded
            else "FAIL"
        )
        lines.append(
            f"{status} stage=path outcome=relay established={relay_established} "
            f"forwarded={forwarded} dropped={dropped}"
        )
    elif relay_ready:
        lines.append(
            "FAIL stage=path outcome=relay_waiting_for_reports "
            f"activated={relay_activated} ready={relay_ready} reports_failed={reports_failed}"
        )
    elif relay_unavailable:
        lines.append(
            "FAIL stage=path outcome=relay_unavailable "
            f"count={relay_unavailable}"
        )
    elif relay_activated:
        lines.append(
            "FAIL stage=path outcome=relay_handshake_incomplete "
            f"activated={relay_activated} ready=0"
        )
    elif paired:
        lines.append(
            "FAIL stage=path outcome=unconfirmed "
            f"reports_failed={reports_failed}"
        )
    else:
        lines.append("MISSING stage=path detail=natneg_not_paired")

    lines.append(
        "INFO stage=rejections "
        f"admission={admission_rejections} protocol={protocol_rejections}"
    )
    for metric in (
        "fruitspy_admission_rejections_total",
        "fruitspy_protocol_rejections_total",
    ):
        for series, delta in _changed_metric_series(before, after, metric):
            lines.append(f"INFO rejection={series} delta={delta}")
    if game_result == "passed":
        lines.append(f"SUMMARY result=passed server_path={path}")
    elif game_result == "failed" and path == "relay" and forwarded and not dropped:
        lines.append(
            "SUMMARY result=failed failure_domain=client_or_gameplay "
            "evidence=relay_forwarding_without_drops"
        )
    elif game_result == "failed" and path == "direct":
        lines.append(
            "SUMMARY result=failed failure_domain=client_or_peer_path "
            "evidence=direct_success_reported"
        )
    elif game_result == "failed":
        lines.append("SUMMARY result=failed failure_domain=server_pipeline")
    else:
        lines.append(f"SUMMARY result=unknown server_path={path}")
    return lines


def _add_endpoint_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=DEFAULT_METRICS_URL)
    parser.add_argument("--timeout", type=float, default=2.0)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fruitspy-diagnostics",
        description="Compare privacy-safe FruitSpy metrics across one game attempt.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture", help="capture the pre-game counter baseline")
    capture.add_argument("snapshot", type=Path)
    _add_endpoint_arguments(capture)
    compare = commands.add_parser("compare", help="compare current counters to a baseline")
    compare.add_argument("snapshot", type=Path)
    compare.add_argument(
        "--game-result",
        choices=("unknown", "passed", "failed"),
        default="unknown",
    )
    _add_endpoint_arguments(compare)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    try:
        current = fetch_counter_samples(args.url, args.timeout)
        if args.command == "capture":
            captured_at = write_snapshot(args.snapshot, current)
            print(
                f"CAPTURED path={args.snapshot} captured_at={captured_at} "
                f"series={len(current)}"
            )
            return
        before = read_snapshot(args.snapshot)
        for line in diagnosis_lines(before, current, args.game_result):
            print(line)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"ERROR {error}\n")


if __name__ == "__main__":
    main()
