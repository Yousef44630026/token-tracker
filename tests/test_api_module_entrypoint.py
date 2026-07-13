"""The collector module entry point starts without import-order warnings."""

from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import create_server, make_http_transport  # noqa: E402
from tests._harness import make_checker  # noqa: E402

check = make_checker()

check(callable(create_server), "lazy package export exposes create_server")
check(callable(make_http_transport), "lazy package export exposes make_http_transport")

result = subprocess.run(
    [sys.executable, "-m", "api.main", "--help"],
    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    capture_output=True,
    text=True,
    timeout=15,
    check=False,
)
check(result.returncode == 0, "python -m api.main --help exits successfully")
check("RuntimeWarning" not in result.stderr, "module entry point emits no import-order warning")

sys.exit(check.report("RESULT test_api_module_entrypoint"))
