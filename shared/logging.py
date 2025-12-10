import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional


def setup_logging(service_name: str, log_dir: str, level: str = "INFO") -> None:
    # Ensure directory exists
    p = Path(log_dir)
    p.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level.upper())

    # Clear existing handlers to avoid duplicates in reload
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt_human = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(level.upper())
    ch.setFormatter(fmt_human)
    root.addHandler(ch)

    # Rotating file handler (daily rotation, keep 7 backups)
    fh = TimedRotatingFileHandler(
        filename=str(p / "app.log"), when="D", interval=1, backupCount=7, encoding="utf-8"
    )
    fh.setLevel(level.upper())
    fh.setFormatter(fmt_human)
    root.addHandler(fh)

    logging.getLogger(__name__).info("logging initialized", extra={"service": service_name})
