"""``python -m sellerclaw_agent`` entry point.

The runtime image installs the dependencies and copies the package to ``/app``,
but not a console script, so the one-command install (``install.sh`` + the
``sellerclaw-agent`` wrapper) drives the CLI through ``docker exec`` with this
module form. The repo checkout keeps using the ``sellerclaw-agent`` console
script declared in ``pyproject.toml`` — both call the same ``cli.main()``.
"""

from __future__ import annotations

from sellerclaw_agent.cli import main

if __name__ == "__main__":
    main()
