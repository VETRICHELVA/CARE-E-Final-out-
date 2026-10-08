"""`uv run python -m app` (from services/iot-ingest): run the ingest until Ctrl-C or SIGTERM."""

import logging
import signal
import threading

from app.config import Settings
from app.ingest import run


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run(Settings(), stop)


if __name__ == "__main__":
    main()
