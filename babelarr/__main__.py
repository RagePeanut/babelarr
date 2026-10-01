"""Babelarr entry point.

Starts the always-on full-library sweep loop, plus the configured new-media
mechanism (webhook / polling / disabled).
"""

from __future__ import annotations

import logging
import os
import signal
import threading

from .config import Config, ConfigError
from .plex_client import connect
from .processor import Processor
from .tmdb import TMDBClient
from . import webhook as webhook_mod

log = logging.getLogger("babelarr")


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _loop(interval_minutes: int, fn, stop: threading.Event, name: str) -> None:
    interval = max(1, interval_minutes) * 60
    while not stop.is_set():
        try:
            fn()
        except Exception:
            log.exception("%s iteration failed", name)
        stop.wait(interval)


def main() -> int:
    _setup_logging()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    log.info(
        "Babelarr starting (mode=%s, sweep=%dm, dry_run=%s, max_channels=%s, "
        "only_replace_unlocked=%s)",
        config.new_media_mode,
        config.sweep_interval_minutes,
        config.dry_run,
        config.max_audio_channels,
        config.only_replace_unlocked,
    )
    for name, rs in (
        ("subtitle", config.subtitle_rules),
        ("poster", config.poster_rules),
        ("title", config.title_rules),
    ):
        if rs.is_empty():
            log.info("No %s rules configured; that concern will be left untouched", name)

    server = connect(config.plex_url, config.plex_token)
    tmdb = TMDBClient(config.tmdb_api_key)
    processor = Processor(config, server, tmdb)

    stop = threading.Event()

    def _handle_signal(signum, frame):
        log.info("Received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    sweep_thread = threading.Thread(
        target=_loop,
        args=(config.sweep_interval_minutes, processor.full_sweep, stop, "full-sweep"),
        daemon=True,
    )
    sweep_thread.start()

    http_server = None
    if config.new_media_mode == "webhook":
        http_server = webhook_mod.serve(processor, config.webhook_port)
    elif config.new_media_mode == "polling":
        threading.Thread(
            target=_loop,
            args=(
                config.recent_poll_interval_minutes,
                processor.recent_sweep,
                stop,
                "recent-poll",
            ),
            daemon=True,
        ).start()

    while not stop.is_set():
        stop.wait(1)

    if http_server is not None:
        http_server.shutdown()
    log.info("Babelarr stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
