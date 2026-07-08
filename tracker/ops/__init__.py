"""Operational readiness helpers."""

from __future__ import annotations

from typing import Any

__all__ = ["DoctorCheck", "run_checks"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from tracker.ops.doctor import DoctorCheck, run_checks

        return {"DoctorCheck": DoctorCheck, "run_checks": run_checks}[name]
    raise AttributeError(name)
