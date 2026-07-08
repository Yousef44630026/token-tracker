"""Typed builder for TokenEvent.observation metadata.

The stored event model keeps ``observation`` as a plain dictionary for compatibility and
extensibility. This helper gives new code a typed path into that dictionary while preserving
the open shape for legacy/custom metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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


@dataclass(frozen=True)
class Observation:
    """Typed operational metadata that serializes to ``TokenEvent.observation``."""

    authoritative: bool = True
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
        known = {
            "status",
            "authoritative",
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
        kwargs = {key: data[key] for key in known if key in data}
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(extra=extra, **kwargs)


def build_observation(**kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper returning a validated observation dictionary."""
    return Observation(**kwargs).to_dict()


__all__ = ["Observation", "build_observation"]
