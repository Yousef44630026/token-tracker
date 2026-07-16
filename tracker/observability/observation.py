"""Typed builder for TokenEvent.observation metadata.

``TokenEvent`` keeps this typed object as its operational authority gate. It also implements
the mutable mapping interface so existing adapters can attach provider-specific metadata
without weakening validation of the known fields.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar

from tracker.observability.status import STATUS_VALUES


def _non_empty_string(name: str, value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{name} must be a non-empty string when provided")


def _non_negative_number(name: str, value: float | int | None) -> None:
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{name} must be a non-negative number when provided")


def _non_negative_int(name: str, value: int | None) -> None:
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer when provided")


@dataclass
class Observation(MutableMapping[str, Any]):
    """Typed operational metadata that serializes to ``TokenEvent.observation``."""

    authoritative: bool = False
    status: str | None = None
    http_status: int | None = None
    duration_ms: float | int | None = None
    time_to_first_token_ms: float | int | None = None
    time_to_last_token_ms: float | int | None = None
    provider_request_id: str | None = None
    provider_response_id: str | None = None
    provider_error_code: str | None = None
    retry_count: int | None = None
    service_name: str | None = None
    tenant_id: str | None = None
    cloud_provider: str | None = None
    region: str | None = None
    deployment: str | None = None
    fallback_from: str | None = None
    fallback_to: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    _FIELD_NAMES: ClassVar[frozenset[str]] = frozenset(
        {
            "authoritative",
            "status",
            "http_status",
            "duration_ms",
            "time_to_first_token_ms",
            "time_to_last_token_ms",
            "provider_request_id",
            "provider_response_id",
            "provider_error_code",
            "retry_count",
            "service_name",
            "tenant_id",
            "cloud_provider",
            "region",
            "deployment",
            "fallback_from",
            "fallback_to",
        }
    )

    def __post_init__(self) -> None:
        if self.status is not None and self.status not in STATUS_VALUES:
            raise ValueError(f"status must be one of: {', '.join(sorted(STATUS_VALUES))}")
        if not isinstance(self.authoritative, bool):
            raise ValueError("authoritative must be a boolean")
        if self.http_status is not None:
            if isinstance(self.http_status, bool) or not isinstance(self.http_status, int) or not 100 <= self.http_status <= 599:
                raise ValueError("http_status must be an integer between 100 and 599")
        for name in (
            "provider_request_id",
            "provider_response_id",
            "provider_error_code",
            "service_name",
            "tenant_id",
            "cloud_provider",
            "region",
            "deployment",
            "fallback_from",
            "fallback_to",
        ):
            _non_empty_string(name, getattr(self, name))
        for name in (
            "duration_ms",
            "time_to_first_token_ms",
            "time_to_last_token_ms",
        ):
            _non_negative_number(name, getattr(self, name))
        _non_negative_int("retry_count", self.retry_count)
        if (self.fallback_from and not self.fallback_to) or (self.fallback_to and not self.fallback_from):
            raise ValueError("fallback_from and fallback_to must be provided together")
        if not isinstance(self.extra, dict):
            raise TypeError("extra must be a dictionary")

    def to_dict(self) -> dict[str, Any]:
        """Return a compact dictionary suitable for TokenEvent.observation."""
        data = dict(self.extra)
        data["authoritative"] = self.authoritative
        for key in (
            "status",
            "http_status",
            "duration_ms",
            "time_to_first_token_ms",
            "time_to_last_token_ms",
            "provider_request_id",
            "provider_response_id",
            "provider_error_code",
            "retry_count",
            "service_name",
            "tenant_id",
            "cloud_provider",
            "region",
            "deployment",
            "fallback_from",
            "fallback_to",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data

    def __getitem__(self, key: str) -> Any:
        if key in self._FIELD_NAMES:
            value = getattr(self, key)
            if value is None:
                raise KeyError(key)
            return value
        return self.extra[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.update({key: value})

    def __delitem__(self, key: str) -> None:
        if key == "authoritative":
            raise ValueError("authoritative cannot be removed")
        data = self.to_dict()
        if key not in data:
            raise KeyError(key)
        del data[key]
        self._replace(self.from_dict(data))

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def update(self, *args: Mapping[str, Any] | object, **kwargs: Any) -> None:
        """Apply a dictionary-style update atomically through full validation."""
        if len(args) > 1:
            raise TypeError(f"update expected at most 1 argument, got {len(args)}")
        data = self.to_dict()
        if args:
            source = args[0]
            if isinstance(source, Mapping):
                data.update(source)
            else:
                data.update(dict(source))  # type: ignore[arg-type]
        data.update(kwargs)
        self._replace(self.from_dict(data))

    def _replace(self, other: Observation) -> None:
        for descriptor in fields(self):
            value = getattr(other, descriptor.name)
            setattr(self, descriptor.name, dict(value) if descriptor.name == "extra" else value)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, require_explicit_authority: bool = True) -> Observation:
        """Validate and normalize an observation dictionary.

        ``authoritative`` is the operational gate for totals. If any observation metadata is
        present, the key must be explicitly present and boolean; otherwise typos such as
        ``authoratative`` would silently default into the total.
        """
        if not isinstance(data, dict):
            raise TypeError("observation must be a dictionary")
        if require_explicit_authority and "authoritative" not in data:
            raise ValueError("observation.authoritative must be explicit")
        known = cls._FIELD_NAMES
        kwargs = {key: data[key] for key in known if key in data}
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(extra=extra, **kwargs)


def build_observation(**kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper returning a validated observation dictionary."""
    return Observation(**kwargs).to_dict()


__all__ = ["Observation", "build_observation"]
