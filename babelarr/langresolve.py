"""Resolve a title's *content language* (what it's actually spoken in).

Babelarr distinguishes two languages for a title:

* **production language** — TMDB's ``original_language``. Tied to the origin
  country / where the title was produced; NOT necessarily the spoken language
  (e.g. the anime *Uzumaki* has ``original_language='en'`` because it was an
  Adult Swim production, while it is spoken in Japanese). Used for posters and
  titles, where "the original poster/title" conventionally means the production
  one.

* **content language** — what the title is actually spoken in, derived from
  TMDB's ``spoken_languages`` with the resolution below. Used for audio and
  subtitles.

Content-language resolution order:

  1. **Manual override** (a ``babelarr-ov:<lang>`` label) — wins outright.
  2. **Exactly one spoken language** -> use it.
  3. **Multiple spoken, and the first == production language** -> first spoken.
  4. **Multiple spoken, first != production** -> walk the spoken list in order
     and pick the first one for which the file has an audio track.
  5. **None of the spoken languages are available as audio** -> first spoken.
  6. **No spoken languages at all** -> production language.

Everything is compared after normalization to canonical ISO 639-3.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .langcodes import normalize


def resolve_content_language(
    production_language: Optional[str],
    spoken_languages: Sequence[Optional[str]],
    available_audio_languages: Sequence[Optional[str]],
    override: Optional[str] = None,
) -> Optional[str]:
    """Return the normalized content language, or ``None`` if undeterminable.

    See the module docstring for the full precedence. ``override`` is the
    normalized (or raw) value from a ``babelarr-ov:<lang>`` label, if present.
    """
    # 1. Manual override wins.
    norm_override = normalize(override)
    if norm_override is not None:
        return norm_override

    prod = normalize(production_language)
    spoken = [normalize(s) for s in spoken_languages]
    spoken = [s for s in spoken if s is not None]

    # 6. No spoken languages -> production language.
    if not spoken:
        return prod

    # 2. Exactly one spoken language.
    if len(spoken) == 1:
        return spoken[0]

    # 3. First spoken equals production language.
    if prod is not None and spoken[0] == prod:
        return spoken[0]

    # 4. Walk spoken list, pick first with an available audio track.
    available = {normalize(a) for a in available_audio_languages}
    available.discard(None)
    for s in spoken:
        if s in available:
            return s

    # 5. None available -> first spoken.
    return spoken[0]
