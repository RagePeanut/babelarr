"""Orchestration: process a single title, or sweep whole libraries.

Shared by every entry point (full sweep, webhook handler, recent poll) so the
behavior is identical regardless of what triggered it. A per-rating-key lock
serializes concurrent work on the same item.

For each title Babelarr applies up to four independent, opt-in concerns. They
are driven by TWO different languages:
  * audio + subtitle track defaults -> the CONTENT language (what the title is
    spoken in; resolved from TMDB spoken_languages + the file's audio tracks,
    with a ``babelarr-ov:<lang>`` label override).
  * poster + display title          -> the PRODUCTION language (TMDB
    original_language), i.e. "the original poster/title".
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import TYPE_CHECKING

from .config import Config
from .plex_client import (
    _MOVIE,
    _SHOW,
    _audio_views,
    _subtitle_views,
    apply_poster,
    apply_selection,
    apply_title,
    content_language_for,
    production_language_for,
    resolve_tmdb_id,
)
from .poster_selector import select_poster
from .selector import select_for_part
from .state import StatePersistence, build_state
from .title_selector import select_title

if TYPE_CHECKING:  # heavy deps (plexapi/requests) only needed for type hints
    from plexapi.server import PlexServer
    from .tmdb import TMDBClient

log = logging.getLogger("babelarr.processor")


def _with_retry(fn, attempts: int, what: str):
    """Call ``fn`` with up to ``attempts`` tries and exponential backoff.

    Returns ``fn()``'s result. Re-raises the last exception if every attempt
    fails. Used for the big Plex enumeration calls that can transiently time
    out on a busy server.
    """
    last_exc = None
    for i in range(1, max(1, attempts) + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - transient Plex/network errors
            last_exc = exc
            if i < attempts:
                backoff = min(30, 2 ** (i - 1))  # 1s, 2s, 4s, ... capped at 30s
                log.warning("%s failed (attempt %d/%d): %s; retrying in %ds",
                            what, i, attempts, exc, backoff)
                time.sleep(backoff)
            else:
                log.error("%s failed after %d attempts: %s", what, attempts, exc)
    raise last_exc


class Processor:
    def __init__(self, config: Config, server: PlexServer, tmdb: TMDBClient,
                 state: StatePersistence = None):
        self.config = config
        self.server = server
        self.tmdb = tmdb
        self.state = state if state is not None else build_state(
            config.state_persistence or "file", config.state_file, config.dry_run
        )
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
            # Content language (spoken) drives audio/subs; production language
            # (TMDB original_language) drives poster/title.
            content = content_language_for(movie, self.tmdb, is_movie=True)
            production = production_language_for(movie, self.tmdb, is_movie=True)
            if content is None and production is None:
                log.debug("No language info for %s; skipping", movie.title)
                return
            log.info("Movie: %s (content=%s, production=%s)",
                     movie.title, content, production)
            if content is not None:
                self._process_tracks(movie, content)
            self._process_poster_and_title(movie, production, is_movie=True)

    def process_show(self, show) -> None:
        # Poster/title use the show's PRODUCTION language.
        production = production_language_for(show, self.tmdb, is_movie=False)
        log.info("Show: %s (production=%s)", show.title, production)
        with self._lock_for(show.ratingKey):
            self._process_poster_and_title(show, production, is_movie=False)
        # Tracks use CONTENT language, resolved PER EPISODE (available audio
        # tracks differ per file). Episodes inherit the series' TMDB metadata.
        try:
            episodes = _with_retry(
                show.episodes, self._retries(),
                f"listing episodes of {show.title!r}",
            )
        except Exception:
            log.exception("Could not list episodes of %s; skipping its tracks",
                          show.title)
            return
        for episode in episodes:
            try:
                with self._lock_for(episode.ratingKey):
                    self.process_episode(episode, show=show)
            except Exception:
                log.exception("Skipping episode %s", getattr(episode, "title", "?"))

    def process_episode(self, episode, show=None) -> None:
        series = show if show is not None else episode.show()
        content = content_language_for(series, self.tmdb, is_movie=False)
        # Re-resolve content language against THIS episode's audio tracks when
        # the series lookup couldn't (multi-spoken titles need the file).
        if content is None:
            return
        with self._lock_for(episode.ratingKey):
            self._process_tracks(episode, content)

    def _process_tracks(self, video, content_language: str) -> None:
        for media in video.media:
            for part in media.parts:
                try:
                    selection = select_for_part(
                        audios=_audio_views(part),
                        subtitles=_subtitle_views(part),
                        original_language=content_language,
                        audio_rules=self.config.audio_rules,
                        subtitle_rules=self.config.subtitle_rules,
                        max_channels=self.config.max_audio_channels,
                    )
                    apply_selection(part, selection, self.config.dry_run)
                except Exception:  # pragma: no cover - defensive
                    log.exception("Failed processing part of %s",
                                  getattr(video, "title", "?"))

    def _process_poster_and_title(self, item, production_language, is_movie: bool) -> None:
        want_poster = not self.config.poster_rules.is_empty()
        want_title = not self.config.title_rules.is_empty()
        if not (want_poster or want_title) or production_language is None:
            return

        tmdb_id = resolve_tmdb_id(item, self.tmdb, is_movie)
        if tmdb_id is None:
            return

        if want_poster:
            try:
                posters = self.tmdb.posters(tmdb_id, is_movie)
                key = select_poster(posters, production_language, self.config.poster_rules)
                if key:
                    apply_poster(item, key, self.config.skip_user_locked,
                                 self.state, self.config.dry_run)
            except Exception:  # pragma: no cover - defensive
                log.exception("Failed poster for %s", getattr(item, "title", "?"))

        if want_title:
            try:
                options = self.tmdb.title_options(tmdb_id, is_movie)
                title = select_title(options, self.config.title_rules)
                if title:
                    apply_title(item, title, self.config.skip_user_locked,
                                self.state, self.config.dry_run)
            except Exception:  # pragma: no cover - defensive
                log.exception("Failed title for %s", getattr(item, "title", "?"))

    # -- sweeps --------------------------------------------------------------

    def _retries(self) -> int:
        return getattr(self.config, "plex_retries", 3)

    def full_sweep(self) -> None:
        log.info("Starting full library sweep")
        processed = skipped = 0
        try:
            sections = _with_retry(
                self._target_sections, self._retries(), "listing library sections"
            )
        except Exception:
            log.exception("Could not list library sections; aborting this sweep")
            return

        for sec in sections:
            log.info("Sweeping section: %s (%s)", sec.title, sec.type)
            try:
                items = _with_retry(
                    sec.all, self._retries(), f"enumerating section {sec.title!r}"
                )
            except Exception:
                # One section failing (e.g. timeout) must not abort the others.
                log.exception("Skipping section %s after repeated failures",
                              sec.title)
                continue

            for item in items:
                try:
                    if sec.type == _MOVIE:
                        self.process_movie(item)
                    elif sec.type == _SHOW:
                        self.process_show(item)
                    processed += 1
                except Exception:
                    # One bad item must not abort the sweep.
                    skipped += 1
                    log.exception("Skipping %s", getattr(item, "title", "?"))

        self.state.flush()
        log.info("Full sweep complete (processed=%d, skipped=%d)", processed, skipped)

    def recent_sweep(self, limit: int = 50) -> None:
        log.info("Polling recently added items")
        processed = skipped = 0
        try:
            sections = _with_retry(
                self._target_sections, self._retries(), "listing library sections"
            )
        except Exception:
            log.exception("Could not list library sections; skipping this poll")
            return

        for sec in sections:
            try:
                try:
                    recent = sec.recentlyAdded(maxresults=limit)
                except TypeError:
                    recent = sec.recentlyAdded()
            except Exception:
                log.exception("Could not fetch recently-added for %s", sec.title)
                continue

            for item in recent:
                try:
                    if sec.type == _MOVIE:
                        self.process_movie(item)
                    elif sec.type == _SHOW:
                        if item.type == "episode":
                            self.process_episode(item)
                        elif item.type == "show":
                            self.process_show(item)
                    processed += 1
                except Exception:
                    skipped += 1
                    log.exception("Skipping %s", getattr(item, "title", "?"))

        self.state.flush()
        log.info("Recent poll complete (processed=%d, skipped=%d)", processed, skipped)

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
        self.state.flush()
