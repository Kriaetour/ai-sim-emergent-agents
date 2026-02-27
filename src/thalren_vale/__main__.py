# (c) 2026 (KriaetvAspie / AspieTheBard)
# Licensed under the Polyform Noncommercial License 1.0.0
"""Entry point for: python -m thalren_vale"""

import os
import sys


def _ensure_hash_seed() -> None:
    """Re-exec with PYTHONHASHSEED=0 when a --seed argument is present.

    Python randomises the hash seed between process invocations by default,
    making dict/set iteration order non-deterministic.  Setting PYTHONHASHSEED=0
    before the interpreter starts guarantees identical ordering across runs with
    the same --seed value, which is required for reproducible experiments.

    Uses subprocess.run() rather than os.execve() so that stdout/stderr are
    correctly inherited on Windows (os.execve does not forward I/O handles
    through PowerShell pipelines on Windows).
    """
    if "--seed" not in sys.argv:
        return
    if os.environ.get("PYTHONHASHSEED") == "0":
        return  # already in a deterministic-hash process
    import subprocess
    env = dict(os.environ, PYTHONHASHSEED="0")
    pkg = __package__ or "thalren_vale"
    result = subprocess.run(
        [sys.executable, "-m", pkg] + sys.argv[1:],
        env=env,
        # Inherit stdin/stdout/stderr from the parent so all output is visible.
    )
    sys.exit(result.returncode)


_ensure_hash_seed()

from .sim import run  # noqa: E402 – import must come after re-exec guard

if __name__ == "__main__":
    run()
