"""Subtitle *format* aliasing for ``SUBTITLE_CODEC_PRIORITY``.

Plex does not publish a fixed vocabulary of subtitle codec strings: the value
exposed on a stream (``SubtitleStream.format`` / ``.codec``) is whatever the
underlying demuxer (ffmpeg) reports. The *same* human format therefore shows up
under several spellings depending on the source container and ffmpeg version --
e.g. SubRip appears as both ``srt`` and ``subrip``; Blu-ray PGS as ``pgs`` and
``hdmv_pgs_subtitle``; DVD VobSub as ``vobsub`` and ``dvd_subtitle``.

To let users write one friendly name and have it match every known spelling,
each *canonical* format maps to a set of raw aliases it should match. When a
user puts ``srt`` in ``SUBTITLE_CODEC_PRIORITY`` we match ``srt`` **and**
``subrip``; ``pgs`` matches ``pgs`` and ``hdmv_pgs_subtitle``; and so on.

This table is intentionally pragmatic and **not** meant to be exhaustive -- the
space of demuxer strings has no authoritative list. Any value a user lists that
is *not* a canonical name is still honored as a literal, so an unmapped format
works immediately; contributing it here (so the friendly name also matches it)
is the natural way the table grows over time.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Set

# Canonical format name -> every raw codec/format string it should match.
# The canonical name itself is always included implicitly (see ``_build``).
# Keep values lower-case; matching is case-insensitive.
_ALIASES: Dict[str, Set[str]] = {
    # --- text-based -------------------------------------------------------
    "srt": {"subrip"},
    # ASS (Advanced SubStation Alpha) is the superset of the older SSA; ffmpeg
    # reports both under either spelling, so treat them as one family keyed on
    # the canonical "ass".
    "ass": {"ssa"},
    "vtt": {"webvtt"},
    "mov_text": {"tx3g", "text"},  # MP4 / 3GPP timed text
    "smi": {"sami"},
    # --- image-based ------------------------------------------------------
    "pgs": {"hdmv_pgs_subtitle"},  # Blu-ray Presentation Graphic Stream
    "vobsub": {"dvd_subtitle", "dvdsub"},  # DVD VobSub
    "dvb": {"dvb_subtitle", "dvbsub"},
    "xsub": set(),  # DivX
}


def _build() -> Dict[str, FrozenSet[str]]:
    """Canonical name -> frozenset of all strings it matches (incl. itself)."""
    out: Dict[str, FrozenSet[str]] = {}
    for canonical, aliases in _ALIASES.items():
        members = {canonical, *(a.lower() for a in aliases)}
        out[canonical] = frozenset(members)
    return out


_RESOLVED: Dict[str, FrozenSet[str]] = _build()


def match_set(entry: str) -> FrozenSet[str]:
    """Return every raw codec string that a priority-list ``entry`` matches.

    If ``entry`` is a known canonical name, this is the canonical name plus all
    of its aliases. Otherwise ``entry`` is treated as a literal raw codec string
    and matches only itself -- so unmapped formats still work, they just don't
    pull in alias spellings until added to :data:`_ALIASES`.
    """
    key = entry.strip().lower()
    if key in _RESOLVED:
        return _RESOLVED[key]
    return frozenset({key})


def canonical_names() -> FrozenSet[str]:
    """All canonical format names known to Babelarr (for docs/diagnostics)."""
    return frozenset(_RESOLVED)
