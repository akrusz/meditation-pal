"""Allow running as python -m src."""

import sys

if __name__ == "__main__":
    if "--web" in sys.argv:
        sys.argv.remove("--web")
        from .web import run_web
        run_web()
    else:
        from .main import main
        main()
