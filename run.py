"""Start FlipIntel locally."""
from __future__ import annotations

import argparse
import logging
import threading
import webbrowser

import uvicorn

from app.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="FlipIntel — UK hardware deal scanner")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    args = parser.parse_args()
    settings.ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    url = f"http://{'localhost' if args.host in {'127.0.0.1', '0.0.0.0'} else args.host}:{args.port}"
    print("\n  FlipIntel — automatic UK hardware deal discovery")
    print(f"  Dashboard: {url}")
    print("  Press Ctrl+C to stop\n")
    if not args.no_browser:
        threading.Timer(1.4, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
