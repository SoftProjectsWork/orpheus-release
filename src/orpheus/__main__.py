"""Allow `python -m orpheus <command>` (works on Windows without the Scripts folder on PATH)."""

from .cli import main

raise SystemExit(main())
