"""Stable fingerprint of the Python runtime source loaded by local services."""

from __future__ import annotations

import hashlib
from pathlib import Path


def runtime_fingerprint(root: str | Path | None = None) -> str:
    """Hash Python source paths and bytes under ``api`` and ``tracker``."""
    project_root = Path(root).expanduser().resolve() if root is not None else Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    file_count = 0
    for directory_name in ("api", "tracker"):
        directory = project_root / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(project_root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            content = path.read_bytes()
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
            file_count += 1
    if file_count == 0:
        raise ValueError(f"no runtime Python sources found under {project_root}")
    return digest.hexdigest()


__all__ = ["runtime_fingerprint"]
