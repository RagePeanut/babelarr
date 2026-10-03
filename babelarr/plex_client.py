"""Plex integration: enumerate libraries, resolve metadata, apply changes.

Wraps python-plexapi. Translates Plex media parts into the lightweight views
consumed by the pure selectors, and writes chosen defaults / posters / titles
back to Plex. Honors the ``SKIP_USER_LOCKED`` guard (via fingerprint state) so
posters/titles the *user* locked by hand are never clobbered, while Babelarr's
own locked values are still re-managed.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Iterable, List, Optional

from .langresolve import resolve_content_language
from .selector import AudioStreamView, SubtitleStreamView, Selection

if TYPE_CHECKING:  # import only for type hints; avoids a hard requests dependency
    from .tmdb import TMDBClient

log = logging.getLogger("babelarr.plex")

_MOVIE = "movie"
_SHOW = "show"

_TMDB_RE = re.compile(r"tmdb://(\d+)")
_IMDB_RE = re.compile(r"imdb://(tt\d+)")
_TVDB_RE = re.compile(r"tvdb://(\d+)")

# Manual content-language override label, e.g. "babelarr-ov:ja".
_OV_LABEL_PREFIX = "babelarr-ov:"


def connect(url: str, token: str, timeout: int = 120):
    from plexapi.server import PlexServer  # lazy: keeps this module importable

    return PlexServer(url, token, timeout=timeout)


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


def production_language_for(item, tmdb: "TMDBClient", is_movie: bool) -> Optional[str]:
    """Resolve a Plex item's PRODUCTION language (TMDB original_language).

    This drives posters and titles ("the original poster/title"). It is NOT
    necessarily the spoken language -- see ``content_language_for``.
    """
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


# Backwards-compatible alias (old name referred to production language).
original_language_for = production_language_for


def ov_override_for(item) -> Optional[str]:
    """Return the content-language override from a ``babelarr-ov:<lang>`` label."""
    for tag in getattr(item, "labels", []) or []:
        name = getattr(tag, "tag", None)
        if name and name.startswith(_OV_LABEL_PREFIX):
            value = name[len(_OV_LABEL_PREFIX):].strip()
            if value:
                return value
    return None


def _available_audio_languages(item) -> List[Optional[str]]:
    """Collect the languages of all audio tracks across an item's parts."""
    langs: List[Optional[str]] = []
    for media in getattr(item, "media", []) or []:
        for part in getattr(media, "parts", []) or []:
            for s in part.audioStreams():
                langs.append(
                    getattr(s, "languageCode", None) or getattr(s, "language", None)
                )
    return langs


def content_language_for(item, tmdb: "TMDBClient", is_movie: bool) -> Optional[str]:
    """Resolve a Plex item's CONTENT language (what it's spoken in).

    Drives audio and subtitles. Resolution: a ``babelarr-ov:<lang>`` label
    override, else TMDB ``spoken_languages`` (with the file's available audio
    tracks as a tiebreaker), falling back to the production language. See
    ``langresolve.resolve_content_language``.
    """
    override = ov_override_for(item)
    tmdb_id = resolve_tmdb_id(item, tmdb, is_movie)
    production, spoken = (None, ())
    if tmdb_id is not None:
        production, spoken = tmdb.language_info(tmdb_id, is_movie)
    return resolve_content_language(
        production_language=production,
        spoken_languages=spoken,
        available_audio_languages=_available_audio_languages(item),
        override=override,
    )


# -- audio/subtitle stream views ---------------------------------------------

def _is_active(s) -> bool:
    """Whether a stream is the one currently in effect.

    Plex exposes ``selected`` (the active stream) and ``default`` (the file's
    default flag); prefer ``selected`` and fall back to ``default``.
    """
    sel = getattr(s, "selected", None)
    if sel is not None:
        return bool(sel)
    return bool(getattr(s, "default", False))


def _audio_views(part) -> List[AudioStreamView]:
    views = []
    for s in part.audioStreams():
        views.append(
            AudioStreamView(
                id=s.id,
                language_code=getattr(s, "languageCode", None) or getattr(s, "language", None),
                channels=int(getattr(s, "audioChannels", 0) or 0),
                codec=getattr(s, "codec", None),
                is_default=_is_active(s),
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
                is_default=_is_active(s),
                forced=bool(getattr(s, "forced", False)),
            )
        )
    return views


def apply_selection(part, selection: Selection, dry_run: bool) -> bool:
    """Apply an audio/subtitle selection to a Plex media part."""
    changed = False

    if selection.audio_stream_id is not None:
        current = next(
            (s.id for s in part.audioStreams() if getattr(s, "selected", None)
             or getattr(s, "default", False)),
            None,
        )
        if current != selection.audio_stream_id:
            log.info("  audio -> stream %s%s", selection.audio_stream_id,
                     " (dry-run)" if dry_run else "")
            if not dry_run:
                part.setSelectedAudioStream(selection.audio_stream_id)
            changed = True

    if selection.disable_subtitles:
        current = next(
            (s.id for s in part.subtitleStreams() if getattr(s, "selected", None)
             or getattr(s, "default", False)),
            None,
        )
        if current is not None:
            log.info("  subtitles -> OFF%s", " (dry-run)" if dry_run else "")
            if not dry_run:
                part.resetSelectedSubtitleStream()
            changed = True
    elif selection.subtitle_stream_id is not None:
        current = next(
            (s.id for s in part.subtitleStreams() if getattr(s, "selected", None)
             or getattr(s, "default", False)),
            None,
        )
        if current != selection.subtitle_stream_id:
            log.info("  subtitles -> stream %s%s", selection.subtitle_stream_id,
                     " (dry-run)" if dry_run else "")
            if not dry_run:
                part.setSelectedSubtitleStream(selection.subtitle_stream_id)
            changed = True

    return changed


# -- poster / title field locking -------------------------------------------

# Plex field names behind each concern (for lock inspection).
_FIELD_TITLE = "title"
_FIELD_POSTER = "thumb"


def _is_field_locked(item, field_name: str) -> bool:
    """True if a metadata field is locked in Plex."""
    for f in getattr(item, "fields", []) or []:
        if getattr(f, "name", None) == field_name and getattr(f, "locked", False):
            return True
    return False


def _user_owns_field(item, field_name: str, current_value, state) -> bool:
    """True if a LOCKED field was last set by the user, not Babelarr.

    A field is "user owned" when it is locked AND its current value does not
    match the fingerprint Babelarr recorded for it. An unlocked field is never
    user-owned (Babelarr is free to set it).
    """
    if not _is_field_locked(item, field_name):
        return False
    return not state.is_ours(item, field_name, current_value)


def _selected_poster_key(item):
    """The ratingKey of the item's currently-selected poster, or ``None``.

    For a poster Babelarr uploaded this is a stable ``upload://posters/<hash>``
    id (hash of the image bytes), persisting across normal scans/refreshes. If
    the user swaps the poster the selected id changes, letting us tell our own
    poster from one the user picked.
    """
    try:
        for p in item.posters():
            if getattr(p, "selected", False):
                return getattr(p, "ratingKey", None)
    except Exception:  # pragma: no cover - network/plex variance
        pass
    return None


def apply_poster(item, poster_url, skip_user_locked, state, dry_run: bool) -> bool:
    """Set a poster, then lock it and fingerprint the resulting selection.

    Skips only when the poster field is locked AND user-owned (its selected
    poster's ratingKey doesn't match what Babelarr recorded) AND ``poster`` is
    in ``skip_user_locked``. Babelarr's own locked posters are re-managed freely.
    """
    if "poster" in skip_user_locked and _user_owns_field(
        item, _FIELD_POSTER, _selected_poster_key(item), state
    ):
        log.debug("  poster user-locked, skipping %s", getattr(item, "title", "?"))
        return False
    log.info("  poster -> %s%s", poster_url, " (dry-run)" if dry_run else "")
    if dry_run:
        return True
    item.uploadPoster(url=poster_url)
    item.lockPoster()
    # P3: read back the now-selected poster's ratingKey and fingerprint THAT, so
    # a later user swap (which changes the selected poster) is detectable.
    try:
        item.reload()
    except Exception:  # pragma: no cover - defensive
        pass
    key = _selected_poster_key(item)
    if key is not None:
        state.record(item, _FIELD_POSTER, key)
    return True


def apply_title(item, title: str, skip_user_locked, state, dry_run: bool) -> bool:
    """Set + lock the display title, then fingerprint it.

    Skips only when the title is locked AND user-owned (current title doesn't
    match our recorded fingerprint) AND ``title`` is in ``skip_user_locked``.
    """
    if "title" in skip_user_locked and _user_owns_field(
        item, _FIELD_TITLE, getattr(item, "title", None), state
    ):
        log.debug("  title user-locked, skipping %s", getattr(item, "title", "?"))
        return False
    already_set = getattr(item, "title", None) == title
    if already_set and _is_field_locked(item, _FIELD_TITLE):
        state.record(item, _FIELD_TITLE, title)  # ensure fingerprint, no write
        return False
    log.info("  title -> %r%s", title, " (dry-run)" if dry_run else "")
    if not dry_run:
        item.editTitle(title, locked=True)
    state.record(item, _FIELD_TITLE, title)
    return True
