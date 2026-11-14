"""Boot a freshly-configured app in a subprocess.

Some settings are read ONCE at import time (RATE_LIMIT, CORS_ORIGINS,
STORAGE_DRIVER, MEDIA_LOCAL_PATH) and wired into middleware / mounts. They can
not be monkeypatched after import, and reloading app modules in-process would
leak state into other tests. Instead each scenario runs in its own interpreter.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import app as _app_pkg

BACKEND_ROOT = str(Path(_app_pkg.__file__).resolve().parents[1])
_MARKER = "__RESULT__"

_TEMPLATE = """
import json
from fastapi.testclient import TestClient
from app.main import app
result = None
{body}
print({marker!r} + json.dumps(result))
"""


def run_app(body: str, env: dict[str, str] | None = None, timeout: int = 60):
    """Run `body` against a fresh app. `body` has `app` and `TestClient` in
    scope and must assign a JSON-serialisable value to `result`."""
    full_env = {**os.environ, **(env or {})}
    full_env["PYTHONPATH"] = os.pathsep.join(filter(None, [BACKEND_ROOT, full_env.get("PYTHONPATH")]))
    script = _TEMPLATE.format(body=textwrap.dedent(body), marker=_MARKER)
    proc = subprocess.run([sys.executable, "-c", script], env=full_env, capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"subprocess app failed (rc={proc.returncode}):\n{proc.stderr[-3000:]}")
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith(_MARKER)][-1]
    return json.loads(line[len(_MARKER):])
