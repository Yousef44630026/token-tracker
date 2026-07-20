"""Command-line entry point for real-call proxy comparisons."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tracker.analytics.provider_validation import (
    build_capability_certification_matrix,
    build_provider_validation_matrix,
    capability_matrix_to_markdown,
    certification_requirement_failures,
    matrix_to_markdown,
    summarize_capability_certification,
    summarize_provider_validation,
)
from tracker.export.powerbi_exporter import export_powerbi_events
from tracker.models.enums import TokenType
from tracker.models.token_event import TokenEvent
from tracker.proxy.codex_logs import import_new_codex_events, snapshot_sessions
from tracker.proxy.estimator import estimate_text
from tracker.proxy.live_usage import LiveUsageTracker
from tracker.proxy.privacy import audit_store, render_privacy_audit
from tracker.proxy.prompt_suite import command_for_prompt, parse_prompt_suite
from tracker.proxy.quality import check_prompt_output, render_quality_summary
from tracker.proxy.report import (
    render_json,
    render_summary,
    summarize_events,
    write_prompt_groups_csv,
)
from tracker.proxy.server import ProxyConfig, create_proxy_server
from tracker.storage.file_repository import FileRepository, PartitionedFileRepository
from tracker.validation.fixture_manifest import PROVIDER_CAPABILITY_POLICIES, realistic_fixture_records


def _quantity(event: TokenEvent, token_type: TokenType) -> int | None:
    return next(
        (q.quantity for q in event.quantities if q.token_type == token_type),
        None,
    )


def _comparison(event: TokenEvent) -> dict | None:
    for quantity in event.quantities:
        comparison = quantity.metadata.get("prompt_estimate")
        if isinstance(comparison, dict):
            return comparison
    return None


def _print_event(event: TokenEvent) -> None:
    comparison = _comparison(event) or {}
    estimate = comparison.get("quantity")
    estimator = comparison.get("estimator")
    exact_input = _quantity(event, TokenType.INPUT)
    cache_read = _quantity(event, TokenType.CACHED_INPUT)
    cache_creation = _quantity(event, TokenType.CACHE_CREATION_INPUT)
    provider_prompt = comparison.get("provider_prompt_tokens", exact_input)
    output = _quantity(event, TokenType.OUTPUT)
    difference = comparison.get("provider_minus_estimate")
    print(
        "tracked"
        f" provider={event.provider}"
        f" surface={event.api_surface}"
        f" model={event.model or 'unknown'}"
        f" estimate={estimate if estimate is not None else 'n/a'}"
        f" estimator={estimator or 'n/a'}"
        f" provider_prompt={provider_prompt if provider_prompt is not None else 'unknown'}"
        f" fresh_input={exact_input if exact_input is not None else 0}"
        f" cache_read={cache_read if cache_read is not None else 0}"
        f" cache_creation={cache_creation if cache_creation is not None else 0}"
        f" provider_output={output if output is not None else 'unknown'}"
        f" delta={difference if difference is not None else 'n/a'}"
        f" contributing_total={event.event_contributing_tokens}",
        flush=True,
    )


def _environment_flag(environment: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = environment.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be one of: true, false, 1, 0, yes, no, on, off")


def _parser(environment: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    env = os.environ if environment is None else environment
    parser = argparse.ArgumentParser(description="Compare TokenTap-style prompt estimates with exact provider usage")
    subcommands = parser.add_subparsers(dest="mode", required=True)

    def add_proxy_options(
        command_parser: argparse.ArgumentParser,
        *,
        default_port: int = 8080,
    ) -> None:
        command_parser.add_argument(
            "--provider",
            required=True,
            help=(
                "provider to proxy; 'anthropic' and 'openai' have dedicated adapters and "
                "default upstreams. Any other provider (groq, together, an OpenAI-compatible "
                "gateway...) is accepted WITH an explicit --upstream: captured via the generic "
                "fallback adapter (real counts kept, unverified, contributing 0 until verified)"
            ),
        )
        command_parser.add_argument("--store", default=env.get("TRACKER_PROXY_STORE", "real_call_events.jsonl"))
        command_parser.add_argument("--host", default=env.get("TRACKER_PROXY_HOST", "127.0.0.1"))
        command_parser.add_argument("--port", type=int, default=env.get("TRACKER_PROXY_PORT", str(default_port)))
        command_parser.add_argument(
            "--durable",
            action=argparse.BooleanOptionalAction,
            default=_environment_flag(env, "TRACKER_PROXY_DURABLE", default=True),
            help="fsync recorded events before acknowledging persistence (default: enabled)",
        )
        command_parser.add_argument("--upstream")
        command_parser.add_argument("--timeout", type=float, default=300.0)
        command_parser.add_argument(
            "--live-budget-tokens",
            type=int,
            help="show a live provider-token usage bar against this token budget",
        )
        command_parser.add_argument(
            "--live-bar-width",
            type=int,
            default=28,
            help="character width for --live-budget-tokens progress bars",
        )

    serve_parser = subcommands.add_parser("serve", help="run the proxy until interrupted")
    add_proxy_options(serve_parser)

    run_parser = subcommands.add_parser("run", help="run one command through the proxy")
    add_proxy_options(run_parser)
    run_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to launch after '--'",
    )

    codex_parser = subcommands.add_parser(
        "codex",
        help="launch Codex normally and import local Codex token-count events",
    )
    codex_parser.add_argument("--store", default="codex_events.jsonl")
    codex_parser.add_argument(
        "--codex-home",
        help="Codex home directory containing sessions/ and state_5.sqlite (defaults to CODEX_HOME or ~/.codex)",
    )
    codex_parser.add_argument(
        "--live-budget-tokens",
        type=int,
        help="show a live provider-token usage bar against this token budget",
    )
    codex_parser.add_argument(
        "--live-bar-width",
        type=int,
        default=28,
        help="character width for --live-budget-tokens progress bars",
    )
    codex_parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex executable to launch",
    )
    codex_parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help=("seconds between local Codex token_count imports while Codex is running; " "set <=0 to import only after exit"),
    )
    codex_parser.add_argument(
        "--include-existing-sessions",
        action="store_true",
        help=(
            "also import token_count lines appended to Codex session files that " "existed before launch; default follows only new sessions"
        ),
    )
    codex_parser.add_argument(
        "--no-report",
        action="store_true",
        help="do not print a tracker report after Codex exits",
    )
    codex_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Codex args after '--' (empty launches interactive Codex)",
    )

    codex_suite_parser = subcommands.add_parser(
        "codex-suite",
        help="run a Markdown prompt suite through Codex and import local token-count events",
    )
    codex_suite_parser.add_argument("--store", default="codex_events.jsonl")
    codex_suite_parser.add_argument("--codex-home")
    codex_suite_parser.add_argument(
        "--live-budget-tokens",
        type=int,
        help="show a live provider-token usage bar against this token budget",
    )
    codex_suite_parser.add_argument("--live-bar-width", type=int, default=28)
    codex_suite_parser.add_argument("--codex-bin", default="codex")
    codex_suite_parser.add_argument("--prompts", required=True)
    codex_suite_parser.add_argument("--start", type=int, default=1)
    codex_suite_parser.add_argument("--limit", type=int)
    codex_suite_parser.add_argument("--dry-run", action="store_true")
    codex_suite_parser.add_argument("--resume-complete", action="store_true")
    codex_suite_parser.add_argument("--fail-fast", action="store_true")
    codex_suite_parser.add_argument("--no-report", action="store_true")
    codex_suite_parser.add_argument(
        "--include-existing-sessions",
        action="store_true",
        help=(
            "also import token_count lines appended to Codex session files that "
            "existed before each prompt; default is safer for isolated suites"
        ),
    )
    codex_suite_parser.add_argument(
        "--suppress-output",
        action="store_true",
        help="capture Codex stdout/stderr without echoing raw child output to the terminal",
    )
    codex_suite_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help=(
            "optional Codex args after '--'; use {prompt} as placeholder. "
            "Default: --no-alt-screen -a never exec --skip-git-repo-check -s read-only {prompt}"
        ),
    )

    suite_parser = subcommands.add_parser(
        "prompt-suite",
        help="run a Markdown prompt suite through one proxied command per prompt",
    )
    add_proxy_options(suite_parser, default_port=0)
    suite_parser.add_argument("--prompts", required=True)
    suite_parser.add_argument(
        "--input-mode",
        choices=("arg", "stdin"),
        default="arg",
        help="pass each prompt as a command argument or via stdin",
    )
    suite_parser.add_argument(
        "--placeholder",
        default="{prompt}",
        help="command argument placeholder replaced by each prompt in arg mode",
    )
    suite_parser.add_argument(
        "--start",
        type=int,
        default=1,
        help="1-based prompt sequence to start from, useful for resuming a suite",
    )
    suite_parser.add_argument("--limit", type=int)
    suite_parser.add_argument("--dry-run", action="store_true")
    suite_parser.add_argument(
        "--resume-complete",
        action="store_true",
        help="skip prompts already completed in the target store",
    )
    suite_parser.add_argument("--fail-fast", action="store_true")
    suite_parser.add_argument(
        "--quality-checks",
        action="store_true",
        help="check child stdout against known scenario expectations without storing it",
    )
    suite_parser.add_argument(
        "--fail-on-quality",
        action="store_true",
        help="return non-zero when any quality check fails",
    )
    suite_parser.add_argument(
        "--suppress-output",
        action="store_true",
        help="capture child stdout/stderr without echoing raw child output to the terminal",
    )
    suite_parser.add_argument("--no-report", action="store_true")
    suite_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to launch after '--'",
    )

    report_parser = subcommands.add_parser(
        "report",
        help="summarize one recorded real-call JSONL file",
    )
    report_parser.add_argument("--store", default="real_call_events.jsonl")
    report_parser.add_argument(
        "--partitioned-store",
        action="store_true",
        help="treat --store as a date/trace partitioned repository root",
    )
    report_parser.add_argument("--json", action="store_true")
    report_parser.add_argument("--per-prompt-csv")

    matrix_parser = subcommands.add_parser(
        "provider-matrix",
        help="show adapter and fixture validation coverage by provider/API surface",
    )
    matrix_parser.add_argument("--json", action="store_true")
    matrix_parser.add_argument(
        "--fail-on-gaps",
        action="store_true",
        help="exit non-zero when any provider/surface has validation gaps",
    )
    matrix_parser.add_argument(
        "--require-proven",
        action="append",
        default=[],
        metavar="PROVIDER:SURFACE[:CAPABILITY]",
        help="exit non-zero unless the selected surface or capability has REAL proof; repeatable",
    )
    matrix_parser.add_argument(
        "--output",
        help="write the provider matrix artifact to this path (Markdown or JSON with --json)",
    )

    powerbi_parser = subcommands.add_parser(
        "powerbi-export",
        help="export a Power BI import folder from one recorded event JSONL store",
    )
    powerbi_parser.add_argument("--store", default="real_call_events.jsonl")
    powerbi_parser.add_argument(
        "--partitioned-store",
        action="store_true",
        help="treat --store as a date/trace partitioned repository root",
    )
    powerbi_parser.add_argument("--output", required=True)
    powerbi_parser.add_argument("--dataset-name", default="ai_token_tracker")

    audit_parser = subcommands.add_parser(
        "privacy-audit",
        help="scan a proxy JSONL for raw prompts or obvious credential leakage",
    )
    audit_parser.add_argument("--store", default="real_call_events.jsonl")
    audit_parser.add_argument("--prompts")
    audit_parser.add_argument("--json", action="store_true")

    count_parser = subcommands.add_parser(
        "count-prompt",
        help="estimate tokens for raw prompt text without making a provider call",
    )
    count_parser.add_argument(
        "text",
        nargs="*",
        help="prompt text to count; omit with --stdin or --interactive",
    )
    count_parser.add_argument(
        "--stdin",
        action="store_true",
        help="read the whole prompt from standard input",
    )
    count_parser.add_argument(
        "--interactive",
        action="store_true",
        help="count one typed line/prompt at a time until Ctrl+Z/Ctrl+C",
    )
    count_parser.add_argument(
        "--budget-tokens",
        type=int,
        help="show an estimate progress bar against this prompt-token budget",
    )
    count_parser.add_argument(
        "--bar-width",
        type=int,
        default=28,
        help="character width for --budget-tokens progress bars",
    )
    count_parser.add_argument("--json", action="store_true")
    count_parser.add_argument(
        "--show-hash",
        action="store_true",
        help="print the prompt SHA-256 prefix in plain-text output",
    )
    return parser


@dataclass(frozen=True, slots=True)
class _ChildRunResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _clean_command(args: argparse.Namespace) -> list[str]:
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    return command


def _render_prompt_budget(
    *,
    used_tokens: int,
    budget_tokens: int,
    width: int,
    delta: int | None = None,
) -> str:
    if budget_tokens <= 0:
        raise SystemExit("--budget-tokens must be positive")
    if width < 8:
        raise SystemExit("--bar-width must be at least 8")
    ratio = min(used_tokens / budget_tokens, 1.0)
    filled = round(ratio * width)
    bar = "#" * filled + "-" * (width - filled)
    left = max(budget_tokens - used_tokens, 0)
    percent = round(used_tokens / budget_tokens * 100, 2)
    delta_text = f" +{delta:,}" if delta is not None else ""
    over = max(used_tokens - budget_tokens, 0)
    over_text = f" over={over:,}" if over else ""
    return f"prompt estimate [{bar}] " f"used={used_tokens:,}/{budget_tokens:,} " f"left={left:,} " f"({percent}%){delta_text}{over_text}"


def _print_prompt_estimate(
    text: str,
    *,
    json_output: bool,
    show_hash: bool,
    sequence: int | None = None,
) -> int:
    estimate = estimate_text(text)
    payload = {
        "tokens": estimate.quantity,
        "estimator": estimate.estimator,
        "characters": estimate.text_characters,
        "sha256": estimate.text_sha256,
    }
    if sequence is not None:
        payload["sequence"] = sequence
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        prefix = f"prompt {sequence}: " if sequence is not None else ""
        hash_text = f" sha256={estimate.text_sha256[:12]}..." if show_hash else ""
        print(
            f"{prefix}tokens={estimate.quantity:,}"
            f" chars={estimate.text_characters:,}"
            f" estimator={estimate.estimator}"
            f"{hash_text}",
            flush=True,
        )
    return estimate.quantity


def _run_count_prompt(args: argparse.Namespace) -> int:
    modes = sum([bool(args.stdin), bool(args.interactive), bool(args.text)])
    if modes == 0:
        raise SystemExit("count-prompt requires text, --stdin, or --interactive")
    if modes > 1:
        raise SystemExit("use only one input mode: text, --stdin, or --interactive")

    budget = args.budget_tokens
    used = 0
    if args.interactive:
        print("Type one prompt per line. Press Ctrl+Z then Enter to finish.", flush=True)
        for sequence, line in enumerate(sys.stdin, start=1):
            text = line.rstrip("\n")
            tokens = _print_prompt_estimate(
                text,
                json_output=args.json,
                show_hash=args.show_hash,
                sequence=sequence,
            )
            used += tokens
            if budget is not None and not args.json:
                print(
                    _render_prompt_budget(
                        used_tokens=used,
                        budget_tokens=budget,
                        width=args.bar_width,
                        delta=tokens,
                    ),
                    flush=True,
                )
        return 0

    text = sys.stdin.read() if args.stdin else " ".join(args.text)
    tokens = _print_prompt_estimate(
        text,
        json_output=args.json,
        show_hash=args.show_hash,
    )
    if budget is not None and not args.json:
        print(
            _render_prompt_budget(
                used_tokens=tokens,
                budget_tokens=budget,
                width=args.bar_width,
                delta=tokens,
            ),
            flush=True,
        )
    return 0


def _config(args: argparse.Namespace, **overrides: object) -> ProxyConfig:
    values = {
        "provider": getattr(args, "provider", "openai"),
        "upstream_base_url": args.upstream,
        "host": args.host,
        "port": args.port,
        "timeout_seconds": args.timeout,
    }
    values.update(overrides)
    return ProxyConfig(**values)


def _read_repository(args: argparse.Namespace):
    return PartitionedFileRepository(args.store) if getattr(args, "partitioned_store", False) else FileRepository(args.store)


def _run_proxied_command(
    *,
    args: argparse.Namespace,
    repository: FileRepository,
    config: ProxyConfig,
    command: list[str],
    stdin_text: str | None = None,
    capture_output: bool = False,
    echo_captured_output: bool = True,
    on_event=None,
) -> _ChildRunResult:
    server = create_proxy_server(repository, config, on_event=on_event or _print_event)
    host, port = server.server_address[:2]
    proxy_url = f"http://{host}:{port}"
    env_name = "ANTHROPIC_BASE_URL" if args.provider == "anthropic" else "OPENAI_BASE_URL"

    print(f"proxy listening on {proxy_url}", flush=True)
    print(f"events: {repository.path}", flush=True)
    print(f"configure {env_name}={proxy_url}", flush=True)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = dict(os.environ)
    environment[env_name] = proxy_url
    try:
        completed = subprocess.run(
            command,
            env=environment,
            check=False,
            input=stdin_text,
            text=capture_output or stdin_text is not None,
            capture_output=capture_output,
        )
        if capture_output and echo_captured_output:
            if completed.stdout:
                print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
            if completed.stderr:
                print(completed.stderr, end="", file=sys.stderr)
        return _ChildRunResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    except FileNotFoundError:
        print(f"command not found: {command[0]}")
        return _ChildRunResult(returncode=127)
    finally:
        server.shutdown()
        server.server_close()


def _completed_prompt_keys(repository: FileRepository) -> set[tuple[int, str]]:
    summary = summarize_events(repository.iter_events())
    completed: set[tuple[int, str]] = set()
    for group in summary.get("prompt_groups", []):
        sequence = group.get("sequence")
        fingerprint = group.get("fingerprint")
        if (
            isinstance(sequence, int)
            and isinstance(fingerprint, str)
            and group.get("exact_usage_events", 0) > 0
            and group.get("incomplete_events", 0) == 0
        ):
            completed.add((sequence, fingerprint))
    return completed


def _live_usage_tracker(args: argparse.Namespace, repository: FileRepository):
    budget = args.live_budget_tokens
    if budget is None:
        return None
    initial_used = summarize_events(repository.iter_events())["contributing_tokens"]
    return LiveUsageTracker(
        budget_tokens=budget,
        used_tokens=initial_used,
        width=args.live_bar_width,
    )


def _event_printer(live_usage: LiveUsageTracker | None):
    def on_event(event: TokenEvent) -> None:
        _print_event(event)
        if live_usage is not None:
            delta = live_usage.observe(event)
            print(live_usage.render(delta=delta), flush=True)

    return on_event


def _codex_command(args: argparse.Namespace) -> list[str]:
    return [args.codex_bin, *_clean_command(args)]


def _default_codex_suite_command() -> list[str]:
    return [
        "--no-alt-screen",
        "-a",
        "never",
        "exec",
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "{prompt}",
    ]


def _import_and_print_codex_events(
    *,
    before: dict[str, int],
    codex_home: str | None,
    repository: FileRepository,
    printer,
    only_new_sessions: bool,
) -> int:
    events = import_new_codex_events(
        before=before,
        codex_home=codex_home,
        only_new_sessions=only_new_sessions,
    )
    imported_ids = repository.append_unique(events)
    for event in events:
        if event.event_id in imported_ids:
            printer(event)
    return len(imported_ids)


def _run_codex(args: argparse.Namespace) -> int:
    repository = FileRepository(args.store)
    live_usage = _live_usage_tracker(args, repository)
    if live_usage is not None:
        print(live_usage.render(), flush=True)
    before = snapshot_sessions(args.codex_home)
    command = _codex_command(args)
    printer = _event_printer(live_usage)
    imported_total = 0
    imported_lock = threading.Lock()
    stop_polling = threading.Event()

    def poll_codex_logs() -> None:
        nonlocal imported_total
        while not stop_polling.wait(args.poll_interval):
            try:
                imported = _import_and_print_codex_events(
                    before=before,
                    codex_home=args.codex_home,
                    repository=repository,
                    printer=printer,
                    only_new_sessions=not args.include_existing_sessions,
                )
            except Exception as exc:  # noqa: BLE001 - live tracking must not kill Codex
                print(f"Codex token watcher warning: {exc}", file=sys.stderr, flush=True)
                continue
            if imported:
                with imported_lock:
                    imported_total += imported

    print(f"events: {repository.path}", flush=True)
    if args.poll_interval > 0:
        print(
            f"launching Codex normally; token_count watcher polls every {args.poll_interval:g}s",
            flush=True,
        )
    else:
        print("launching Codex normally; local token_count events will be imported after exit", flush=True)
    if not _clean_command(args):
        print("Codex interactive mode: type prompts normally; exit Codex for the final report.", flush=True)
    poller = None
    if args.poll_interval > 0:
        poller = threading.Thread(target=poll_codex_logs, daemon=True)
        poller.start()
    try:
        returncode = subprocess.run(command, check=False).returncode
    except FileNotFoundError:
        print(f"command not found: {args.codex_bin}")
        returncode = 127
    finally:
        stop_polling.set()
        if poller is not None:
            poller.join(timeout=max(args.poll_interval, 1.0))

    imported = _import_and_print_codex_events(
        before=before,
        codex_home=args.codex_home,
        repository=repository,
        printer=printer,
        only_new_sessions=not args.include_existing_sessions,
    )
    with imported_lock:
        imported_total += imported
        total = imported_total
    print(f"imported Codex token_count events: {total}", flush=True)

    if not args.no_report:
        print(render_summary(summarize_events(repository.iter_events())))
    return returncode


def _tag_suite_events(events: list[TokenEvent], prompt) -> None:
    for event in events:
        event.observation.update(
            {
                "suite_prompt_sequence": prompt.sequence,
                "suite_prompt_label": prompt.label,
                "suite_prompt_fingerprint": prompt.fingerprint,
                "suite_prompt_source": prompt.source,
            }
        )


def _run_codex_suite(args: argparse.Namespace) -> int:
    all_prompts = parse_prompt_suite(args.prompts)
    if args.start <= 0:
        raise SystemExit("--start must be positive")
    prompts = all_prompts[args.start - 1 :]
    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit must be positive")
        prompts = prompts[: args.limit]
    if not prompts:
        raise SystemExit("no prompts found")

    repository = FileRepository(args.store)
    live_usage = _live_usage_tracker(args, repository)
    if live_usage is not None:
        print(live_usage.render(), flush=True)
    completed = _completed_prompt_keys(repository) if args.resume_complete else set()
    suite_total = len(all_prompts)
    base_command = _clean_command(args) or _default_codex_suite_command()
    print(
        f"codex suite: {args.prompts} ({len(prompts)}/{suite_total} prompts)",
        flush=True,
    )
    failures = 0
    skipped = 0
    printer = _event_printer(live_usage)
    for prompt in prompts:
        if (prompt.sequence, prompt.fingerprint) in completed:
            skipped += 1
            print(
                "skip" f" {prompt.sequence}/{suite_total}" f" label={prompt.label!r}" " reason=already-complete",
                flush=True,
            )
            continue
        child_args = command_for_prompt(base_command, prompt.prompt)
        print(
            "prompt"
            f" {prompt.sequence}/{suite_total}"
            f" label={prompt.label!r}"
            f" chars={prompt.character_count}"
            f" sha256={prompt.fingerprint[:12]}...",
            flush=True,
        )
        if args.dry_run:
            print("codex command:", " ".join([args.codex_bin, *child_args]), flush=True)
            continue

        before = snapshot_sessions(args.codex_home)
        try:
            result = subprocess.run(
                [args.codex_bin, *child_args],
                check=False,
                capture_output=args.suppress_output,
                text=args.suppress_output,
            )
            returncode = result.returncode
        except FileNotFoundError:
            print(f"command not found: {args.codex_bin}")
            returncode = 127

        events = import_new_codex_events(
            before=before,
            codex_home=args.codex_home,
            only_new_sessions=not args.include_existing_sessions,
        )
        _tag_suite_events(events, prompt)
        imported_ids = repository.append_unique(events)
        for event in events:
            if event.event_id in imported_ids:
                printer(event)
        print(f"imported Codex token_count events: {len(imported_ids)}", flush=True)

        if returncode:
            failures += 1
            if args.fail_fast:
                break

    if args.dry_run:
        if skipped:
            print(f"skipped complete prompts: {skipped}", flush=True)
        return 0
    if not args.no_report:
        print(render_summary(summarize_events(repository.iter_events())))
    if skipped:
        print(f"skipped complete prompts: {skipped}", flush=True)
    return 1 if failures else 0


def _run_prompt_suite(args: argparse.Namespace) -> int:
    all_prompts = parse_prompt_suite(args.prompts)
    if args.start <= 0:
        raise SystemExit("--start must be positive")
    suite_total = len(all_prompts)
    prompts = all_prompts[args.start - 1 :]
    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit must be positive")
        prompts = prompts[: args.limit]
    if not prompts:
        raise SystemExit("no prompts found")

    command = _clean_command(args)
    if not args.dry_run and not command:
        raise SystemExit("prompt-suite requires a command after '--'")

    repository = FileRepository(args.store, durable=args.durable)
    live_usage = _live_usage_tracker(args, repository)
    if live_usage is not None:
        print(live_usage.render(), flush=True)
    completed = _completed_prompt_keys(repository) if args.resume_complete else set()
    print(
        f"prompt suite: {args.prompts} ({len(prompts)}/{suite_total} prompts)",
        flush=True,
    )
    failures = 0
    skipped = 0
    quality_results = []
    for prompt in prompts:
        if (prompt.sequence, prompt.fingerprint) in completed:
            skipped += 1
            print(
                "skip" f" {prompt.sequence}/{suite_total}" f" label={prompt.label!r}" " reason=already-complete",
                flush=True,
            )
            continue
        print(
            "prompt"
            f" {prompt.sequence}/{suite_total}"
            f" label={prompt.label!r}"
            f" chars={prompt.character_count}"
            f" sha256={prompt.fingerprint[:12]}...",
            flush=True,
        )
        if args.dry_run:
            continue
        child_command = (
            command_for_prompt(
                command,
                prompt.prompt,
                placeholder=args.placeholder,
            )
            if args.input_mode == "arg"
            else command
        )
        result = _run_proxied_command(
            args=args,
            repository=repository,
            config=_config(
                args,
                prompt_suite_sequence=prompt.sequence,
                prompt_suite_label=prompt.label,
                prompt_suite_fingerprint=prompt.fingerprint,
                prompt_suite_source=prompt.source,
            ),
            command=child_command,
            stdin_text=prompt.prompt if args.input_mode == "stdin" else None,
            capture_output=args.quality_checks or args.suppress_output,
            echo_captured_output=not args.suppress_output,
            on_event=_event_printer(live_usage),
        )
        if args.quality_checks:
            quality_results.append(
                check_prompt_output(
                    sequence=prompt.sequence,
                    label=prompt.label,
                    stdout=result.stdout,
                )
            )
        if result.returncode:
            failures += 1
            if args.fail_fast:
                break

    if args.dry_run:
        if skipped:
            print(f"skipped complete prompts: {skipped}", flush=True)
        return 0
    if not args.no_report:
        print(render_summary(summarize_events(repository.iter_events())))
    if skipped:
        print(f"skipped complete prompts: {skipped}", flush=True)
    quality_failures = 0
    if args.quality_checks:
        print(render_quality_summary(quality_results))
        quality_failures = sum(1 for result in quality_results if not result.passed)
    if args.fail_on_quality and quality_failures:
        return 1
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "report":
        repository = _read_repository(args)
        summary = summarize_events(repository.iter_events())
        if args.per_prompt_csv:
            write_prompt_groups_csv(summary, args.per_prompt_csv)
            print(f"per-prompt CSV: {os.path.abspath(args.per_prompt_csv)}")
        print(render_json(summary) if args.json else render_summary(summary))
        return 0
    if args.mode == "provider-matrix":
        fixture_records = realistic_fixture_records()
        matrix = build_provider_validation_matrix(fixture_records)
        summary = summarize_provider_validation(matrix)
        capability_matrix = build_capability_certification_matrix(
            fixture_records,
            PROVIDER_CAPABILITY_POLICIES,
        )
        capability_summary = summarize_capability_certification(capability_matrix)
        try:
            requirement_failures = certification_requirement_failures(
                matrix,
                capability_matrix,
                args.require_proven,
            )
        except ValueError as exc:
            print(f"provider-matrix: {exc}", file=sys.stderr)
            return 2
        if args.json:
            rendered = json.dumps(
                {
                    "summary": summary,
                    "matrix": matrix,
                    "capability_summary": capability_summary,
                    "capability_matrix": capability_matrix,
                    "requirement_failures": requirement_failures,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        else:
            summary_lines = [
                "Provider validation readiness",
                f"overall_status: {summary['overall_status']}",
                f"surfaces: {summary['surface_count']}",
                f"pass/warn/fail: {summary['pass_count']}/{summary['warn_count']}/{summary['fail_count']}",
                "",
                matrix_to_markdown(matrix),
                "",
                "Provider capability certification",
                (
                    "proven/simulated/unvalidated/unsupported: "
                    f"{capability_summary['proven_count']}/"
                    f"{capability_summary['simulated_count']}/"
                    f"{capability_summary['unvalidated_count']}/"
                    f"{capability_summary['unsupported_count']}"
                ),
                "",
                capability_matrix_to_markdown(capability_matrix),
            ]
            if requirement_failures:
                summary_lines.extend(["", "Unmet required proof: " + ", ".join(requirement_failures)])
            rendered = "\n".join(summary_lines)
        if args.output:
            output_dir = os.path.dirname(args.output)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(rendered)
            print(f"provider matrix: {os.path.abspath(args.output)}")
        else:
            print(rendered)
        has_gaps = any(row["gaps"] for row in matrix)
        return 1 if (args.fail_on_gaps and has_gaps) or requirement_failures else 0
    if args.mode == "powerbi-export":
        repository = _read_repository(args)
        paths = export_powerbi_events(
            repository.iter_events(),
            args.output,
            dataset_name=args.dataset_name,
        )
        print(f"Power BI export: {os.path.abspath(args.output)}")
        print(f"tables/files: {len(paths)}")
        print(f"manifest: {os.path.abspath(paths['manifest'])}")
        print(f"measures: {os.path.abspath(paths['measures'])}")
        return 0
    if args.mode == "privacy-audit":
        result = audit_store(args.store, prompts_path=args.prompts)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if args.json else render_privacy_audit(result))
        return 0 if result["passed"] else 1
    if args.mode == "count-prompt":
        return _run_count_prompt(args)
    if args.mode == "prompt-suite":
        return _run_prompt_suite(args)
    if args.mode == "codex":
        return _run_codex(args)
    if args.mode == "codex-suite":
        return _run_codex_suite(args)

    repository = FileRepository(args.store, durable=args.durable)
    live_usage = _live_usage_tracker(args, repository)
    if live_usage is not None:
        print(live_usage.render(), flush=True)
    config = _config(args)
    server = create_proxy_server(
        repository,
        config,
        on_event=_event_printer(live_usage),
    )
    host, port = server.server_address[:2]
    proxy_url = f"http://{host}:{port}"
    env_name = "ANTHROPIC_BASE_URL" if args.provider == "anthropic" else "OPENAI_BASE_URL"

    print(f"proxy listening on {proxy_url}", flush=True)
    print(f"events: {repository.path}", flush=True)
    print(f"configure {env_name}={proxy_url}", flush=True)

    if args.mode == "serve":
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    command = _clean_command(args)
    if not command:
        server.server_close()
        raise SystemExit("mode=run requires a command after '--'")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = dict(os.environ)
    environment[env_name] = proxy_url
    try:
        return subprocess.run(command, env=environment, check=False).returncode
    except FileNotFoundError:
        print(f"command not found: {command[0]}")
        return 127
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
