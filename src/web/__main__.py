"""Allow running the web interface as python -m src.web."""

from .app import run_web

if __name__ == "__main__":
    run_web()
