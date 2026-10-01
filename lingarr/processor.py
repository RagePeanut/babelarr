"""Orchestration: process a single title, or sweep whole libraries.

Shared by every entry point (full sweep, webhook handler, recent poll) so the
selection logic is identical regardless of what triggered it. A per-rating-key
lock serializes concurrent work on the same item (e.g. a webhook firing during
a full sweep).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import List, Optional

from plexapi.server import PlexServer

from .config import Config
from .plex_client import (
    _MOVIE,
    _SHOW,
    _audio_views,
    _subtitle_views,
    apply_selection,
    original_language_for,
)
from .selector import select_for_part
from .tmdb import TMDBClient

log = logging.getLogger("lingarr.processor")


class Processor:
    def __init__(self, config: Config, server: PlexServer, tmdb: TMDBClient):
        self.config = config
        self.server = server
        self.tmdb = tmdb
        self._locks = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    def _lock_for(self, rating_key) -> threading.Lock:
        with self._locks_guard:
            return self._locks[str(rating_key)]

    # -- library enumeration -------------------------------------------------

    def _target_sections(self):
        sections = self.server.library.sections()
        wanted_types = (_MOVIE, _SHOW)
        selected = []
        for sec in sections:
            if sec.type not in wanted_types:
                continue
            if self.config.libraries and sec.title not in self.config.libraries:
                continue
            selected.append(sec)
        return selected

    # -- processing ----------------------------------------------------------

    def process_movie(self, movie) -> None:
        with self._lock_for(movie.ratingKey):
            orig = original_language_for(movie, self.tmdb, is_movie=True)
            if orig is None:
                log.debug("No original language for %s; skipping", movie.title)
                return
            log.info("Movie: %s (orig=%s)", movie.title, orig)
            self._process_video(movie, orig)

    def process_show(self, show) -> None:
        orig = original_language_for(show, self.tmdb, is_movie=False)
        if orig is None:
            log.debug("No original language for show %s; skipping", show.title)
            return
        log.info("Show: %s (orig=%s)", show.title, orig)
        for episode in show.episodes():
            with self._lock_for(episode.ratingKey):
                self._process_video(episode, orig)

    def process_episode(self, episode) -> None:
        show = episode.show()
        orig = original_language_for(show, self.tmdb, is_movie=False)
        if orig is None:
            return
        with self._lock_for(episode.ratingKey):
            self._process_video(episode, orig)

    def _process_video(self, video, original_language: str) -> None:
        for media in video.media:
            for part in media.parts:
                try:
                    selection = select_for_part(
                        audios=_audio_views(part),
                        subtitles=_subtitle_views(part),
                        original_language=original_language,
                        rules=self.config.subtitle_rules,
                        max_channels=self.config.max_audio_channels,
                    )
                    apply_selection(part, selection, self.config.dry_run)
                except Exception:  # pragma: no cover - defensive
                    log.exception("Failed processing part of %s", getattr(video, "title", "?"))

    # -- sweeps --------------------------------------------------------------

    def full_sweep(self) -> None:
        log.info("Starting full library sweep")
        for sec in self._target_sections():
            log.info("Sweeping section: %s (%s)", sec.title, sec.type)
            if sec.type == _MOVIE:
                for movie in sec.all():
                    self.process_movie(movie)
            elif sec.type == _SHOW:
                for show in sec.all():
                    self.process_show(show)
        log.info("Full sweep complete")

    def recent_sweep(self, limit: int = 50) -> None:
        log.info("Polling recently added items")
        for sec in self._target_sections():
            try:
                recent = sec.recentlyAdded(maxresults=limit)
            except TypeError:
                recent = sec.recentlyAdded()
            for item in recent:
                if sec.type == _MOVIE:
                    self.process_movie(item)
                elif sec.type == _SHOW:
                    # recentlyAdded on a show section yields episodes.
                    if item.type == "episode":
                        self.process_episode(item)
                    elif item.type == "show":
                        self.process_show(item)

    def process_rating_key(self, rating_key) -> None:
        """Process a single item by ratingKey (used by the webhook handler)."""
        item = self.server.fetchItem(int(rating_key))
        if item.type == "movie":
            self.process_movie(item)
        elif item.type == "episode":
            self.process_episode(item)
        elif item.type == "show":
            self.process_show(item)
        else:
            log.debug("Ignoring webhook item of type %s", item.type)
