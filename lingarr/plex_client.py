"""Plex integration: enumerate libraries, resolve original language, apply tracks.

Wraps python-plexapi. Translates Plex media parts into the lightweight stream
views consumed by ``selector`` and writes the chosen defaults back.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional

from plexapi.server import PlexServer

from .selector import AudioStreamView, SubtitleStreamView, Selection, select_for_part
from .tmdb import TMDBClient

log = logging.getLogger("lingarr.plex")

# Library types we handle.
_MOVIE = "movie"
_SHOW = "show"

_TMDB_RE = re.compile(r"tmdb://(\d+)")
_IMDB_RE = re.compile(r"imdb://(tt\d+)")
_TVDB_RE = re.compile(r"tvdb://(\d+)")


def connect(url: str, token: str) -> PlexServer:
    return PlexServer(url, token)


def _guids(item) -> List[str]:
    out = []
    # Newer Plex exposes .guids (list); older exposes a single .guid string.
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


def original_language_for(item, tmdb: TMDBClient, is_movie: bool) -> Optional[str]:
    """Resolve a Plex item's original language via TMDB.

    Falls back to Plex's own metadata if TMDB lookup fails.
    """
    guids = _guids(item)
    tmdb_id = _extract_tmdb_id(guids)

    if tmdb_id is None:
        ext = _extract_external(guids)
        if ext:
            found = tmdb.find_by_external_id(ext[0], ext[1])
            if found:
                bucket = "movie_results" if is_movie else "tv_results"
                results = found.get(bucket) or []
                if results:
                    tmdb_id = results[0].get("id")

    if tmdb_id is not None:
        lang = (
            tmdb.movie_original_language(tmdb_id)
            if is_movie
            else tmdb.tv_original_language(tmdb_id)
        )
        if lang:
            return lang

    # No reliable fallback: Plex's own original-language metadata is sparse and
    # inconsistent, so if TMDB can't resolve it we skip (caller leaves tracks
    # untouched) rather than guess.
    return None


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
    """Apply a selection to a Plex media part. Returns True if anything changed."""
    changed = False

    if selection.audio_stream_id is not None:
        current = next(
            (s.id for s in part.audioStreams() if getattr(s, "default", False)), None
        )
        if current != selection.audio_stream_id:
            log.info(
                "  audio -> stream %s%s",
                selection.audio_stream_id,
                " (dry-run)" if dry_run else "",
            )
            if not dry_run:
                part.setDefaultAudioStream(selection.audio_stream_id)
            changed = True

    if selection.disable_subtitles:
        current = next(
            (s.id for s in part.subtitleStreams() if getattr(s, "default", False)),
            None,
        )
        if current is not None:
            log.info("  subtitles -> OFF%s", " (dry-run)" if dry_run else "")
            if not dry_run:
                part.resetDefaultSubtitleStream()
            changed = True
    elif selection.subtitle_stream_id is not None:
        current = next(
            (s.id for s in part.subtitleStreams() if getattr(s, "default", False)),
            None,
        )
        if current != selection.subtitle_stream_id:
            log.info(
                "  subtitles -> stream %s%s",
                selection.subtitle_stream_id,
                " (dry-run)" if dry_run else "",
            )
            if not dry_run:
                part.setDefaultSubtitleStream(selection.subtitle_stream_id)
            changed = True

    return changed
