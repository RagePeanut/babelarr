"""Plex integration: enumerate libraries, resolve metadata, apply changes.

Wraps python-plexapi. Translates Plex media parts into the lightweight views
consumed by the pure selectors, and writes chosen defaults / posters / titles
back to Plex. Honors the ``ONLY_REPLACE_UNLOCKED`` guard so hand-picked
(locked) posters and titles are never clobbered.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional

from plexapi.server import PlexServer

from .selector import AudioStreamView, SubtitleStreamView, Selection
from .tmdb import TMDBClient

log = logging.getLogger("babelarr.plex")

_MOVIE = "movie"
_SHOW = "show"

_TMDB_RE = re.compile(r"tmdb://(\d+)")
_IMDB_RE = re.compile(r"imdb://(tt\d+)")
_TVDB_RE = re.compile(r"tvdb://(\d+)")


def connect(url: str, token: str) -> PlexServer:
    return PlexServer(url, token)


def _guids(item) -> List[str]:
    out = []
    for g in getattr(item, "guids", []) or []:
        gid = getattr(g, "id", None)
        if gid:
            out.append(gid)
    legacy = getattr(item, "guid", None)
    if legacy:
        out.append(legacy)
    return out


def _extract_tmdb_id(guids: Iterable[str]) -> Optional[int]:
    for g in guids:
        m = _TMDB_RE.search(g)
        if m:
            return int(m.group(1))
    return None


def _extract_external(guids: Iterable[str]) -> Optional[tuple]:
    for g in guids:
        m = _IMDB_RE.search(g)
        if m:
            return (m.group(1), "imdb_id")
    for g in guids:
        m = _TVDB_RE.search(g)
        if m:
            return (m.group(1), "tvdb_id")
    return None


def resolve_tmdb_id(item, tmdb: TMDBClient, is_movie: bool) -> Optional[int]:
    """Resolve a Plex item to a TMDB id (direct guid or via /find)."""
    guids = _guids(item)
    tmdb_id = _extract_tmdb_id(guids)
    if tmdb_id is not None:
        return tmdb_id
    ext = _extract_external(guids)
    if ext:
        found = tmdb.find_by_external_id(ext[0], ext[1])
        if found:
            bucket = "movie_results" if is_movie else "tv_results"
            results = found.get(bucket) or []
            if results:
                return results[0].get("id")
    return None


def original_language_for(item, tmdb: TMDBClient, is_movie: bool) -> Optional[str]:
    """Resolve a Plex item's original language via TMDB."""
    tmdb_id = resolve_tmdb_id(item, tmdb, is_movie)
    if tmdb_id is not None:
        lang = (
            tmdb.movie_original_language(tmdb_id)
            if is_movie
            else tmdb.tv_original_language(tmdb_id)
        )
        if lang:
            return lang
    return None


# -- audio/subtitle stream views ---------------------------------------------

def _audio_views(part) -> List[AudioStreamView]:
    views = []
    for s in part.audioStreams():
        views.append(
            AudioStreamView(
                id=s.id,
                language_code=getattr(s, "languageCode", None) or getattr(s, "language", None),
                channels=int(getattr(s, "audioChannels", 0) or 0),
                codec=getattr(s, "codec", None),
                is_default=bool(getattr(s, "default", False)),
            )
        )
    return views


def _subtitle_views(part) -> List[SubtitleStreamView]:
    views = []
    for s in part.subtitleStreams():
        views.append(
            SubtitleStreamView(
                id=s.id,
                language_code=getattr(s, "languageCode", None) or getattr(s, "language", None),
                is_default=bool(getattr(s, "default", False)),
                forced=bool(getattr(s, "forced", False)),
            )
        )
    return views


def apply_selection(part, selection: Selection, dry_run: bool) -> bool:
    """Apply an audio/subtitle selection to a Plex media part."""
    changed = False

    if selection.audio_stream_id is not None:
        current = next(
            (s.id for s in part.audioStreams() if getattr(s, "default", False)), None
        )
        if current != selection.audio_stream_id:
            log.info("  audio -> stream %s%s", selection.audio_stream_id,
                     " (dry-run)" if dry_run else "")
            if not dry_run:
                part.setDefaultAudioStream(selection.audio_stream_id)
            changed = True

    if selection.disable_subtitles:
        current = next(
            (s.id for s in part.subtitleStreams() if getattr(s, "default", False)), None
        )
        if current is not None:
            log.info("  subtitles -> OFF%s", " (dry-run)" if dry_run else "")
            if not dry_run:
                part.resetDefaultSubtitleStream()
            changed = True
    elif selection.subtitle_stream_id is not None:
        current = next(
            (s.id for s in part.subtitleStreams() if getattr(s, "default", False)), None
        )
        if current != selection.subtitle_stream_id:
            log.info("  subtitles -> stream %s%s", selection.subtitle_stream_id,
                     " (dry-run)" if dry_run else "")
            if not dry_run:
                part.setDefaultSubtitleStream(selection.subtitle_stream_id)
            changed = True

    return changed


# -- poster / title field locking -------------------------------------------

def _is_field_locked(item, field_name: str) -> bool:
    """True if a metadata field is locked in Plex (user hand-picked it)."""
    for f in getattr(item, "fields", []) or []:
        if getattr(f, "name", None) == field_name and getattr(f, "locked", False):
            return True
    return False


def apply_poster(item, poster_url: str, only_unlocked: bool, dry_run: bool) -> bool:
    """Upload/select a poster by URL. Skips locked posters when guarded."""
    if only_unlocked and _is_field_locked(item, "thumb"):
        log.debug("  poster locked, skipping %s", getattr(item, "title", "?"))
        return False
    log.info("  poster -> %s%s", poster_url, " (dry-run)" if dry_run else "")
    if not dry_run:
        item.uploadPoster(url=poster_url)
    return True


def apply_title(item, title: str, only_unlocked: bool, dry_run: bool) -> bool:
    """Set and lock the display title. Skips locked titles when guarded."""
    if only_unlocked and _is_field_locked(item, "title"):
        log.debug("  title locked, skipping %s", getattr(item, "title", "?"))
        return False
    if getattr(item, "title", None) == title:
        return False
    log.info("  title -> %r%s", title, " (dry-run)" if dry_run else "")
    if not dry_run:
        # Lock the field so Plex's agent won't revert it on the next refresh.
        item.editTitle(title, locked=True)
    return True
