"""Local rotating logs; no authentication payloads or passwords are recorded."""
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading

_fault_stream = None


def log_directory():
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "RemoteBelieveMesh/logs"


def configure(version):
    global _fault_stream
    directory = log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("RemoteBelieve")
    if not logger.handlers:
        handler = RotatingFileHandler(directory / "RemoteBelieve.log", maxBytes=2 * 1024 * 1024,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    if _fault_stream is None:
        _fault_stream = (directory / "native-faults.log").open("a", encoding="utf-8")
        faulthandler.enable(file=_fault_stream)
    sys.excepthook = lambda *exc: logger.critical("Unhandled application exception", exc_info=exc)
    threading.excepthook = lambda args: logger.error("Unhandled worker exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    logger.info("Starting Remote Believe %s", version)
    return directory
