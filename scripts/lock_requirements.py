"""
Write requirements.lock: the exact version of every package requirements.txt pulls in,
read from the environment this runs in (the one ProjectStack is known to work with).

The Docker image installs this file as is (pip install --no-deps), so it gets the same
versions instead of whatever is newest on the day it is built.

    .venv/bin/python scripts/lock_requirements.py > requirements.lock

Run it again after upgrading a package (and run the tests).
"""

from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

HEADER = """\
# Exact versions ProjectStack is tested with: every package requirements.txt pulls in.
# Install exactly these:                pip install --no-deps -r requirements.lock
# Regenerate after upgrading a package: .venv/bin/python scripts/lock_requirements.py > requirements.lock
"""


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def closure(requirements: list[Requirement]) -> dict[str, tuple[str, str]]:
    """{normalised name: (name, version)} for the requirements and all their dependencies."""
    env = default_environment()
    found: dict[str, tuple[str, str]] = {}
    extras: dict[str, set[str]] = {}
    stack = list(requirements)
    while stack:
        req = stack.pop()
        try:
            dist = metadata.distribution(req.name)
        except metadata.PackageNotFoundError:
            sys.exit(f"{req.name} is not installed here — install requirements.txt first")
        key = _key(req.name)
        new = set(req.extras) - extras.get(key, set())
        if key in found and not new:
            continue
        found[key] = (dist.metadata["Name"], dist.version)
        extras.setdefault(key, set()).update(req.extras)
        for line in dist.requires or []:
            dep = Requirement(line)
            if dep.marker and not any(dep.marker.evaluate({**env, "extra": e}) for e in extras[key] | {""}):
                continue
            stack.append(dep)
    return found


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    lines = (root / "requirements.txt").read_text().splitlines()
    reqs = [Requirement(line) for line in (x.strip() for x in lines) if line and not line.startswith("#")]
    pins = closure(reqs)
    sys.stdout.write(HEADER + "".join(f"{name}=={version}\n" for _, (name, version) in sorted(pins.items())))


if __name__ == "__main__":
    main()
