"""Explicit archive-first retention for flat and partitioned JSONL repositories."""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from tracker.models.token_event import TokenEvent
from tracker.storage._locking import lock_for
from tracker.storage.file_repository import FileRepository, PartitionedFileRepository

DEFAULT_MAX_STORE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_AGE_DAYS = 30.0
RETENTION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RetentionPolicy:
    """Rotation and optional purge thresholds; purge is disabled unless explicit."""

    max_store_bytes: int | None = DEFAULT_MAX_STORE_BYTES
    max_age_days: float | None = DEFAULT_MAX_AGE_DAYS
    purge_after_days: float | None = None
    purge_enabled: bool = False

    def __post_init__(self) -> None:
        for name in ("max_store_bytes", "max_age_days", "purge_after_days"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value <= 0):
                raise ValueError(f"{name} must be positive or None")
        if self.purge_enabled and self.purge_after_days is None:
            raise ValueError("purge_after_days is required when purge_enabled=True")


@dataclass(frozen=True)
class RetentionReport:
    timestamp: str
    state_file: str
    partitioned: bool
    active_file_count: int
    rotated_segments: tuple[str, ...]
    purged_segments: tuple[str, ...]

    @property
    def rotated_segment_count(self) -> int:
        return len(self.rotated_segments)

    @property
    def purged_segment_count(self) -> int:
        return len(self.purged_segments)

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "rotated_segment_count": self.rotated_segment_count,
            "purged_segment_count": self.purged_segment_count,
        }


@dataclass(frozen=True)
class RetentionStatus:
    state_file: str
    retention_has_run: bool
    last_run_timestamp: str | None
    active_size_bytes: int
    archive_size_bytes: int
    total_size_bytes: int
    segment_count: int
    archive_segment_count: int
    oldest_event_timestamp: str | None
    oldest_event_age_days: float | None
    oldest_active_event_timestamp: str | None
    oldest_active_event_age_days: float | None


def _utc_now(now: dt.datetime | None) -> dt.datetime:
    current = now or dt.datetime.now(dt.UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.UTC)
    return current.astimezone(dt.UTC)


def _state_path(store: str, *, partitioned: bool) -> Path:
    target = Path(store).expanduser().resolve()
    return target / ".retention.json" if partitioned else Path(f"{target}.retention.json")


def _active_paths(store: str, *, partitioned: bool) -> list[Path]:
    target = Path(store).expanduser().resolve()
    if not partitioned:
        return [target]
    if not target.exists():
        return []
    return sorted(path for path in target.rglob("events.jsonl") if path.is_file())


def _archive_paths(active_paths: list[Path]) -> list[Path]:
    archives: list[Path] = []
    for active in active_paths:
        archive_dir = Path(f"{active}.archive")
        if archive_dir.is_dir():
            archives.extend(sorted(archive_dir.glob("*.jsonl.gz")))
    return archives


def _parse_timestamp(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _validate_active_jsonl(raw: bytes, path: Path) -> dt.datetime | None:
    if raw and not raw.endswith(b"\n"):
        raise ValueError(f"active JSONL has an incomplete trailing line: {path}")
    oldest: dt.datetime | None = None
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
            event = TokenEvent.from_dict(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"active JSONL row {line_number} is invalid: {path}: {type(exc).__name__}") from exc
        timestamp = _parse_timestamp(event.timestamp)
        if timestamp is not None and (oldest is None or timestamp < oldest):
            oldest = timestamp
    return oldest


def _should_rotate(raw: bytes, oldest: dt.datetime | None, policy: RetentionPolicy, now: dt.datetime) -> bool:
    size_due = policy.max_store_bytes is not None and len(raw) > policy.max_store_bytes
    age_due = (
        policy.max_age_days is not None
        and oldest is not None
        and (now - oldest).total_seconds() > policy.max_age_days * 86_400
    )
    return bool(raw) and (size_due or age_due)


def _archive_active(active: Path, raw: bytes, *, now: dt.datetime) -> Path:
    archive_dir = Path(f"{active}.archive")
    archive_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()[:16]
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    destination = archive_dir / f"segment-{stamp}-{digest}.jsonl.gz"
    if not destination.exists():
        descriptor, temporary = tempfile.mkstemp(prefix=".retention-", suffix=".tmp", dir=archive_dir)
        try:
            with os.fdopen(descriptor, "wb") as output:
                with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
                    compressed.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            os.utime(destination, (now.timestamp(), now.timestamp()))
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    descriptor, replacement = tempfile.mkstemp(prefix=f".{active.name}.", suffix=".tmp", dir=active.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.flush()
            os.fsync(output.fileno())
        os.replace(replacement, active)
    except BaseException:
        try:
            os.unlink(replacement)
        except FileNotFoundError:
            pass
        raise
    return destination


def _atomic_write_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run_retention(
    store: str,
    policy: RetentionPolicy | None = None,
    *,
    partitioned: bool = False,
    now: dt.datetime | None = None,
) -> RetentionReport:
    """Run one explicit retention pass; archiving precedes active-file replacement."""
    selected_policy = policy or RetentionPolicy()
    current = _utc_now(now)
    target = Path(store).expanduser().resolve()
    lock_target = target / ".repository" if partitioned else target
    rotated: list[str] = []
    purged: list[str] = []
    with lock_for(str(lock_target)):
        active_paths = _active_paths(str(target), partitioned=partitioned)
        for active in active_paths:
            with lock_for(str(active)):
                try:
                    raw = active.read_bytes()
                except FileNotFoundError:
                    continue
                oldest = _validate_active_jsonl(raw, active)
                if _should_rotate(raw, oldest, selected_policy, current):
                    rotated.append(str(_archive_active(active, raw, now=current)))

        archive_paths = _archive_paths(active_paths)
        if selected_policy.purge_enabled:
            assert selected_policy.purge_after_days is not None
            cutoff = current.timestamp() - selected_policy.purge_after_days * 86_400
            for archive in archive_paths:
                if archive.stat().st_mtime < cutoff:
                    archive.unlink()
                    purged.append(str(archive))

        state_file = _state_path(str(target), partitioned=partitioned)
        payload: dict[str, object] = {
            "schema_version": RETENTION_SCHEMA_VERSION,
            "timestamp": current.isoformat(),
            "partitioned": partitioned,
            "policy": asdict(selected_policy),
            "active_file_count": len(active_paths),
            "rotated_segments": rotated,
            "purged_segments": purged,
        }
        _atomic_write_state(state_file, payload)

    return RetentionReport(
        timestamp=current.isoformat(),
        state_file=str(state_file),
        partitioned=partitioned,
        active_file_count=len(active_paths),
        rotated_segments=tuple(rotated),
        purged_segments=tuple(purged),
    )


def inspect_retention(
    store: str,
    *,
    partitioned: bool = False,
    now: dt.datetime | None = None,
) -> RetentionStatus:
    """Inspect physical segments and oldest source event without persisting derived values."""
    current = _utc_now(now)
    target = Path(store).expanduser().resolve()
    active_paths = _active_paths(str(target), partitioned=partitioned)
    archives = _archive_paths(active_paths)
    active_size = sum(path.stat().st_size for path in active_paths if path.exists())
    archive_size = sum(path.stat().st_size for path in archives if path.exists())
    oldest_active: dt.datetime | None = None
    for active in active_paths:
        try:
            active_oldest = _validate_active_jsonl(active.read_bytes(), active)
        except FileNotFoundError:
            continue
        if active_oldest is not None and (oldest_active is None or active_oldest < oldest_active):
            oldest_active = active_oldest
    repository = PartitionedFileRepository(str(target)) if partitioned else FileRepository(str(target))
    oldest: dt.datetime | None = None
    for event in repository.iter_events():
        timestamp = _parse_timestamp(event.timestamp)
        if timestamp is not None and (oldest is None or timestamp < oldest):
            oldest = timestamp

    state_file = _state_path(str(target), partitioned=partitioned)
    last_run: str | None = None
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"retention state is unreadable: {state_file}: {type(exc).__name__}") from exc
        if not isinstance(state, dict) or state.get("schema_version") != RETENTION_SCHEMA_VERSION:
            raise ValueError(f"retention state has an unsupported schema: {state_file}")
        raw_timestamp = state.get("timestamp")
        if not isinstance(raw_timestamp, str) or _parse_timestamp(raw_timestamp) is None:
            raise ValueError(f"retention state has an invalid timestamp: {state_file}")
        last_run = raw_timestamp

    age_days = (current - oldest).total_seconds() / 86_400 if oldest is not None else None
    active_age_days = (current - oldest_active).total_seconds() / 86_400 if oldest_active is not None else None
    active_segments = sum(1 for path in active_paths if path.exists() and path.stat().st_size > 0)
    return RetentionStatus(
        state_file=str(state_file),
        retention_has_run=last_run is not None,
        last_run_timestamp=last_run,
        active_size_bytes=active_size,
        archive_size_bytes=archive_size,
        total_size_bytes=active_size + archive_size,
        segment_count=active_segments + len(archives),
        archive_segment_count=len(archives),
        oldest_event_timestamp=oldest.isoformat() if oldest is not None else None,
        oldest_event_age_days=age_days,
        oldest_active_event_timestamp=oldest_active.isoformat() if oldest_active is not None else None,
        oldest_active_event_age_days=active_age_days,
    )


__all__ = [
    "DEFAULT_MAX_AGE_DAYS",
    "DEFAULT_MAX_STORE_BYTES",
    "RetentionPolicy",
    "RetentionReport",
    "RetentionStatus",
    "inspect_retention",
    "run_retention",
]
