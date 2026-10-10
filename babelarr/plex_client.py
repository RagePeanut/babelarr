"""Plex integration: enumerate libraries, resolve metadata, apply changes.

Wraps python-plexapi. Translates Plex media parts into the lightweight views
consumed by the pure selectors, and writes chosen defaults / posters / titles /
logos / backdrops back to Plex. Honors the ``SKIP_USER_LOCKED`` guard (via
fingerprint state) so poster/title/logo/backdrop values the *user* locked by
hand are never clobbered, while Babelarr's own locked values are still
re-managed.
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


def content_language_for(item, tmdb: "TMDBClient", is_movie: bool) -> Optional[str]:
    """Resolve a Plex item's CONTENT language (what it's spoken in).

    Drives audio and subtitles. Resolution: a ``babelarr-ov:<lang>`` label
    override, else TMDB ``spoken_languages`` (preferring the production language
    when it is among them), falling back to the production language. Returns the
    ``NO_LANGUAGE`` sentinel for silent/no-dialogue titles. See
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


def _current_stream_id(streams, allow_default_fallback: bool = True) -> Optional[int]:
    """Id of the stream currently in effect, or None.

    ``selected`` is what Plex is *actually* using right now; ``default`` is a
    static flag the muxer baked into the file. We always look for a ``selected``
    stream FIRST across all streams (a file commonly marks one track ``default``
    while a *different* track is actually ``selected`` -- see Akira: French
    ``default`` audio vs. the selected Japanese track).

    ``allow_default_fallback`` controls what happens when **nothing** is
    selected:

    * **Audio** (``True``): a video always plays *some* audio, so when no track
      is selected the file ``default`` is what will play -- returning it lets us
      skip a redundant re-set when the default already matches the target.
    * **Subtitles** (``False``): a subtitle that is merely ``default`` but not
      ``selected`` is **not showing** -- subtitles are simply OFF. Falling back
      to ``default`` here would (a) make the "subs already off?" check think a
      sub is active and reset it every sweep, and (b) make the "wanted sub
      already active?" check skip actually enabling it. So subtitles must treat
      "nothing selected" as ``None`` (off), ignoring ``default`` entirely.
    """
    streams = list(streams)
    for s in streams:
        if getattr(s, "selected", False):
            return s.id
    if allow_default_fallback:
        for s in streams:
            if getattr(s, "default", False):
                return s.id
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
                is_default=_is_active(s),
            )
        )
    return views


# Matches the English word "forced" in a subtitle track's title (whole word,
# case-insensitive), e.g. "Français [FORCED]". Used only as a fallback for
# poorly-tagged files (see _subtitle_views).
_FORCED_TITLE_RE = re.compile(r"\bforced\b", re.IGNORECASE)


def _title_marks_forced(s) -> bool:
    """True if a subtitle stream's title/display text contains "forced"."""
    for attr in ("title", "extendedDisplayTitle", "displayTitle"):
        text = getattr(s, attr, None)
        if text and _FORCED_TITLE_RE.search(text):
            return True
    return False


def _subtitle_views(part) -> List[SubtitleStreamView]:
    streams = list(part.subtitleStreams())
    # Prefer the proper ``forced`` track flag. Only if NO track in this part
    # carries it do we fall back to detecting the word "forced" in titles --
    # this is a well-structured-file check: when at least one track is properly
    # flagged we trust the flags and ignore the (often unreliable) titles.
    any_flagged = any(bool(getattr(s, "forced", False)) for s in streams)
    views = []
    for s in streams:
        if any_flagged:
            forced = bool(getattr(s, "forced", False))
        else:
            forced = _title_marks_forced(s)
        views.append(
            SubtitleStreamView(
                id=s.id,
                language_code=getattr(s, "languageCode", None) or getattr(s, "language", None),
                is_default=_is_active(s),
                forced=forced,
                # Prefer the subtitle-specific ``format`` (ex: "srt"); fall back
                # to the generic stream ``codec`` when ``format`` is absent.
                codec=getattr(s, "format", None) or getattr(s, "codec", None),
            )
        )
    return views


def apply_selection(part, selection: Selection, dry_run: bool) -> bool:
    """Apply an audio/subtitle selection to a Plex media part."""
    changed = False

    if selection.audio_stream_id is not None:
        current = _current_stream_id(part.audioStreams())
        if current != selection.audio_stream_id:
            log.info("  audio -> stream %s%s", selection.audio_stream_id,
                     " (dry-run)" if dry_run else "")
            if not dry_run:
                part.setSelectedAudioStream(selection.audio_stream_id)
            changed = True

    if selection.disable_subtitles:
        # A subtitle is "on" only if actually selected; a merely-default track
        # is NOT showing. Ignore default so we don't reset off subs every sweep.
        current = _current_stream_id(
            part.subtitleStreams(), allow_default_fallback=False
        )
        if current is not None:
            log.info("  subtitles -> OFF%s", " (dry-run)" if dry_run else "")
            if not dry_run:
                part.resetSelectedSubtitleStream()
            changed = True
    elif selection.subtitle_stream_id is not None:
        current = _current_stream_id(
            part.subtitleStreams(), allow_default_fallback=False
        )
        if current != selection.subtitle_stream_id:
            log.info("  subtitles -> stream %s%s", selection.subtitle_stream_id,
                     " (dry-run)" if dry_run else "")
            if not dry_run:
                part.setSelectedSubtitleStream(selection.subtitle_stream_id)
            changed = True

    return changed


# -- poster / title field locking -------------------------------------------

# Plex field names behind each concern (for lock inspection). Posters lock as
# ``thumb``; logos (Plex "clearLogo") as ``clearLogo``; backdrops (Plex "art")
# as ``art``; the display title as ``title``.
_FIELD_TITLE = "title"
_FIELD_POSTER = "thumb"
_FIELD_LOGO = "clearLogo"
_FIELD_ART = "art"
# Secondary state slot: the intended source URL of an image Babelarr UPLOADED.
# Only needed for uploads, where the resulting key (upload://<hash>) differs
# from the source URL; for a *selected* image the resulting key already IS the
# URL, so no second value is recorded. Lets us answer "does the uploaded image
# correspond to the URL the rules want now?" (rule-change detection). Each
# image concern gets its own ``<field>-url`` slot.
_FIELD_POSTER_URL = "thumb-url"
_FIELD_LOGO_URL = "clearLogo-url"
_FIELD_ART_URL = "art-url"


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


def _label_tags(labels) -> list:
    """Tag strings from a plexapi label list (or our fakes)."""
    return [getattr(l, "tag", None) for l in (labels or []) if getattr(l, "tag", None)]


# -- generic image concern (poster / logo / backdrop) -----------------------
#
# Posters, logos (Plex "clearLogo") and backdrops (Plex "art") are applied
# identically: Plex exposes candidate images (``posters()`` / ``logos()`` /
# ``arts()``) whose ``ratingKey``/``key`` equals the TMDB source URL, so a wanted
# image is often already a candidate and can be *selected* rather than
# re-uploaded. The only variation is the method/field names, captured in an
# ``_ImageConcern`` so one implementation serves all three.


class _ImageConcern:
    """Method/field names binding the generic image logic to one Plex concern."""

    __slots__ = ("field", "url_field", "skip_key", "candidates", "set", "upload",
                 "lock", "delete", "lock_optional")

    def __init__(self, field, url_field, skip_key, candidates, set_, upload, lock,
                 delete, lock_optional=False):
        self.field = field          # lock field name, e.g. "thumb" / "clearLogo"
        self.url_field = url_field  # secondary state slot for uploads
        self.skip_key = skip_key    # SKIP_USER_LOCKED key, e.g. "poster"
        self.candidates = candidates  # method name returning candidate images
        self.set = set_             # method name to select an existing candidate
        self.upload = upload        # method name to upload from a URL
        self.lock = lock            # method name to lock the field
        self.delete = delete        # method name to clear the image (or None)
        # Some Plex fields can't be locked via the metadata edit endpoint. For
        # `clearLogo`, `<field>.locked=1` returns HTTP 400 (the field has no
        # lock mechanism; Plex keeps a user-set logo without one). When True we
        # TOLERATE a lock failure instead of letting it abort the apply.
        self.lock_optional = lock_optional


_POSTER_CONCERN = _ImageConcern(
    field=_FIELD_POSTER, url_field=_FIELD_POSTER_URL, skip_key="poster",
    candidates="posters", set_="setPoster", upload="uploadPoster",
    lock="lockPoster", delete=None,
)
_LOGO_CONCERN = _ImageConcern(
    field=_FIELD_LOGO, url_field=_FIELD_LOGO_URL, skip_key="logo",
    candidates="logos", set_="setLogo", upload="uploadLogo",
    lock="lockLogo", delete="deleteLogo", lock_optional=True,
)
_ART_CONCERN = _ImageConcern(
    field=_FIELD_ART, url_field=_FIELD_ART_URL, skip_key="backdrop",
    candidates="arts", set_="setArt", upload="uploadArt",
    lock="lockArt", delete="deleteArt",
)


def _try_lock(item, concern: _ImageConcern) -> None:
    """Lock the concern's field, tolerating a failure when lock is optional.

    Plex rejects locking some newer fields (notably ``clearLogo``) with HTTP
    400 -- the field simply has no lock mechanism, yet a user-/API-set image
    still sticks without one. For such concerns (``lock_optional``) a failed
    lock is logged and swallowed so the apply still records its state (and so
    does not re-upload every sweep). For lockable concerns the error propagates.
    """
    try:
        getattr(item, concern.lock)()
    except Exception:
        if not concern.lock_optional:
            raise
        log.debug("  %s field not lockable on this Plex server (ignored) for %s",
                  concern.field, getattr(item, "title", "?"))


def _find_existing_image(item, concern: _ImageConcern, image_url):
    """Return an existing candidate matching ``image_url``, or None.

    Plex lists TMDB candidates with their ``ratingKey``/``key`` equal to the
    source URL, so a wanted TMDB image is often already present -- we can select
    it instead of re-uploading.
    """
    try:
        candidates = getattr(item, concern.candidates)()
    except Exception:  # pragma: no cover - network/plex variance
        return None
    for p in candidates or []:
        if image_url in (getattr(p, "ratingKey", None), getattr(p, "key", None)):
            return p
    return None


def _selected_image_key(item, concern: _ImageConcern):
    """Key of the item's currently-selected image for this concern, or ``None``.

    For an image Babelarr *selected* from the candidates this is the source URL
    (TMDB candidates expose their URL as the ratingKey); for an *uploaded* one
    it's an ``upload://.../<hash>`` id. Either way it describes the image
    actually in effect, so we can tell our own image from a user's swap and
    detect when a rule now wants a different one.
    """
    try:
        for p in getattr(item, concern.candidates)():
            if getattr(p, "selected", False):
                return getattr(p, "ratingKey", None) or getattr(p, "key", None)
    except Exception:  # pragma: no cover - network/plex variance
        pass
    return None


def _apply_image_off(item, concern: _ImageConcern, skip_user_locked, state,
                     dry_run: bool) -> bool:
    """Clear the image (logo ``off`` token) so Plex falls back to its default.

    Mirrors :func:`_apply_image` ownership logic: a user-locked image we don't
    own is left alone when protected. Clearing deletes the image and locks the
    field (so Plex's agent won't re-pick one), fingerprinting the empty state so
    a later rule change that wants an image again is detectable, and so we don't
    re-clear every sweep.
    """
    current_key = _selected_image_key(item, concern)
    locked = _is_field_locked(item, concern.field)
    # Fingerprint of "cleared" is recorded against a stable sentinel value.
    off_sentinel = "babelarr:off"
    # "ours" when either an image we set is still in effect, OR our last
    # recorded state for this field is the cleared sentinel (no image selected).
    already_off = state.is_ours(item, concern.field, off_sentinel)
    ours = already_off or state.is_ours(item, concern.field, current_key)

    if locked and not ours and concern.skip_key in skip_user_locked:
        log.debug("  %s user-locked, skipping %s", concern.skip_key,
                  getattr(item, "title", "?"))
        return False
    if already_off:
        log.debug("  %s already cleared by Babelarr, skipping %s",
                  concern.skip_key, getattr(item, "title", "?"))
        return False

    log.info("  %s -> off (clear)%s", concern.skip_key,
             " (dry-run)" if dry_run else "")
    if dry_run:
        return True
    labels_before = _label_tags(getattr(item, "labels", None))
    deleter = getattr(item, concern.delete, None)
    if deleter is not None:
        try:
            deleter()
        except Exception:  # pragma: no cover - network/plex variance
            log.exception("  could not clear %s for %s", concern.skip_key,
                          getattr(item, "title", "?"))
            return False
    _try_lock(item, concern)  # lock so the agent won't re-pick one (if lockable)
    try:
        item.reload()
    except Exception:  # pragma: no cover - defensive
        pass
    state.record(item, concern.field, off_sentinel, extra_tags=labels_before)
    state.clear(item, concern.url_field, extra_tags=labels_before)
    return True


def _apply_image(item, concern: _ImageConcern, image_url, skip_user_locked, state,
                 dry_run: bool) -> bool:
    """Set this concern's image to ``image_url``, lock it, fingerprint the result.

    Shared by posters, logos and backdrops. Records the **resulting selected
    key** (the source URL when selected from candidates, else the ``upload://``
    id). For an UPLOADED image, whose key differs from the source URL, it ALSO
    records the intended URL (``<field>-url``) so a later rule change is
    detectable; for a selected image that second value is unnecessary (the key
    already IS the URL) and is not written.

    Behavior for a locked field:
    * not ``ours`` (selected key != recorded fingerprint) -> user hand-picked/
      swapped; skip when this concern is in ``skip_user_locked``.
    * ``ours`` and already the wanted image -> skip (no re-set every sweep).
    * ``ours`` but the rules now want a different image -> re-apply.

    Setting prefers selecting an existing candidate over re-uploading.
    """
    current_key = _selected_image_key(item, concern)
    locked = _is_field_locked(item, concern.field)
    ours = state.is_ours(item, concern.field, current_key)
    # "wanted" = the image in effect corresponds to this run's URL. For a
    # selected image the key IS the URL (field match); for an uploaded one we
    # compare the separately-recorded source URL (<field>-url).
    wanted = (
        state.is_ours(item, concern.field, image_url)
        or state.is_ours(item, concern.url_field, image_url)
    )

    # User hand-picked / swapped (locked, and the on-disk image isn't ours).
    if locked and not ours and concern.skip_key in skip_user_locked:
        log.debug("  %s user-locked, skipping %s", concern.skip_key,
                  getattr(item, "title", "?"))
        return False

    # Already our image AND it already corresponds to the wanted URL -> skip.
    if ours and wanted:
        log.debug("  %s already set by Babelarr, skipping %s",
                  concern.skip_key, getattr(item, "title", "?"))
        return False

    existing = _find_existing_image(item, concern, image_url)
    verb = "select" if existing is not None else "upload"
    log.info("  %s -> %s (%s)%s", concern.skip_key, image_url, verb,
             " (dry-run)" if dry_run else "")
    if dry_run:
        return True
    # Snapshot the labels as they are on the server *now*, before any reload.
    # The label state self-heals by removing prior lock labels for the field,
    # reading them from ``item.labels`` -- but ``item.reload()`` below can
    # return a copy that doesn't yet reflect a lock label written on a PREVIOUS
    # run (Plex is eventually-consistent on label writes). If the stale
    # post-reload list drove the self-heal, the old fingerprint label would
    # never be removed and duplicates would accumulate. So we pass these
    # pre-reload tags into record/clear so the self-heal sees the true state.
    labels_before = _label_tags(getattr(item, "labels", None))
    if existing is not None:
        # Wanted image is already a candidate (e.g. a TMDB image Plex knows)
        # -- select it instead of re-downloading/uploading.
        getattr(item, concern.set)(existing)
    else:
        getattr(item, concern.upload)(url=image_url)
    _try_lock(item, concern)
    try:
        item.reload()
    except Exception:  # pragma: no cover - defensive
        pass
    resulting = _selected_image_key(item, concern) or image_url
    state.record(item, concern.field, resulting, extra_tags=labels_before)
    # Only an upload needs the extra URL record (resulting key != URL). For a
    # selected image the key already equals the URL, so skip the redundancy and
    # clear any stale <field>-url from a previous upload.
    if resulting != image_url:
        state.record(item, concern.url_field, image_url, extra_tags=labels_before)
    else:
        state.clear(item, concern.url_field, extra_tags=labels_before)
    return True


# -- public image apply wrappers --------------------------------------------


def apply_poster(item, poster_url, skip_user_locked, state, dry_run: bool) -> bool:
    """Set + lock the poster (``thumb``). See :func:`_apply_image`."""
    return _apply_image(item, _POSTER_CONCERN, poster_url, skip_user_locked,
                        state, dry_run)


def apply_logo(item, logo_url, skip_user_locked, state, dry_run: bool) -> bool:
    """Set + lock the logo (``clearLogo``).

    ``logo_url`` may be the :data:`babelarr.image_selector.SELECT_OFF` sentinel,
    meaning "clear the logo" so Plex falls back to the text display title; it is
    dispatched to :func:`_apply_image_off`. Otherwise behaves like a poster.
    """
    from .image_selector import SELECT_OFF

    if logo_url == SELECT_OFF:
        return _apply_image_off(item, _LOGO_CONCERN, skip_user_locked, state,
                                dry_run)
    return _apply_image(item, _LOGO_CONCERN, logo_url, skip_user_locked, state,
                        dry_run)


def apply_backdrop(item, art_url, skip_user_locked, state, dry_run: bool) -> bool:
    """Set + lock the backdrop (Plex ``art``). See :func:`_apply_image`."""
    return _apply_image(item, _ART_CONCERN, art_url, skip_user_locked, state,
                        dry_run)


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
