"""Orchestration: process a single title, or sweep whole libraries.

Shared by every entry point (full sweep, webhook handler, recent poll) so the
behavior is identical regardless of what triggered it. A per-rating-key lock
serializes concurrent work on the same item.

For each title Babelarr applies up to three independent, opt-in concerns, each
driven by the title's TMDB original language:
  * audio + subtitle track defaults (per media part)
  * poster        (per item)
  * display title (per item)
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict

from plexapi.server import PlexServer

from .config import Config
from .plex_client import (
    _MOVIE,
    _SHOW,
    _audio_views,
    _subtitle_views,
    apply_poster,
    apply_selection,
    apply_title,
    original_language_for,
    resolve_tmdb_id,
)
from .poster_selector import select_poster
from .selector import select_for_part
from .title_selector import select_title
from .tmdb import TMDBClient

log = logging.getLogger("babelarr.processor")


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
        wanted_types = (_MOVIE, _SHOW)
        selected = []
        for sec in self.server.library.sections():
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
            self._process_tracks(movie, orig)
            self._process_poster_and_title(movie, orig, is_movie=True)

    def process_show(self, show) -> None:
        orig = original_language_for(show, self.tmdb, is_movie=False)
        if orig is None:
            log.debug("No original language for show %s; skipping", show.title)
            return
        log.info("Show: %s (orig=%s)", show.title, orig)
        # Poster/title rules apply to the show itself and to each episode's
        # tracks. (Episodes inherit the series' original language.)
        with self._lock_for(show.ratingKey):
            self._process_poster_and_title(show, orig, is_movie=False)
        for episode in show.episodes():
            with self._lock_for(episode.ratingKey):
                self._process_tracks(episode, orig)

    def process_episode(self, episode) -> None:
        show = episode.show()
        orig = original_language_for(show, self.tmdb, is_movie=False)
        if orig is None:
            return
        with self._lock_for(episode.ratingKey):
            self._process_tracks(episode, orig)

    def _process_tracks(self, video, original_language: str) -> None:
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
                    log.exception("Failed processing part of %s",
                                  getattr(video, "title", "?"))

    def _process_poster_and_title(self, item, original_language: str, is_movie: bool) -> None:
        want_poster = not self.config.poster_rules.is_empty()
        want_title = not self.config.title_rules.is_empty()
        if not (want_poster or want_title):
            return

        tmdb_id = resolve_tmdb_id(item, self.tmdb, is_movie)
        if tmdb_id is None:
            return

        if want_poster:
            try:
                posters = self.tmdb.posters(tmdb_id, is_movie)
                key = select_poster(posters, original_language, self.config.poster_rules)
                if key:
                    apply_poster(item, key, self.config.only_replace_unlocked,
                                 self.config.dry_run)
            except Exception:  # pragma: no cover - defensive
                log.exception("Failed poster for %s", getattr(item, "title", "?"))

        if want_title:
            try:
                options = self.tmdb.title_options(tmdb_id, is_movie)
                title = select_title(options, self.config.title_rules)
                if title:
                    apply_title(item, title, self.config.only_replace_unlocked,
                                self.config.dry_run)
            except Exception:  # pragma: no cover - defensive
                log.exception("Failed title for %s", getattr(item, "title", "?"))

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
                    if item.type == "episode":
                        self.process_episode(item)
                    elif item.type == "show":
                        self.process_show(item)

    def process_rating_key(self, rating_key) -> None:
        """Process a single item by ratingKey (webhook handler)."""
        item = self.server.fetchItem(int(rating_key))
        if item.type == "movie":
            self.process_movie(item)
        elif item.type == "episode":
            self.process_episode(item)
        elif item.type == "show":
            self.process_show(item)
        else:
            log.debug("Ignoring webhook item of type %s", item.type)
