"""Console entry point for the Windows build."""

import sys

from tankextract.cli import main

if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("tankextract", "frozen" if getattr(sys, "frozen", False) else "source", flush=True)
    raise SystemExit(main())
