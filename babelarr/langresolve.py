"""Resolve a title's *content language* (what it's actually spoken in).

Babelarr distinguishes two languages for a title:

* **production language** — TMDB's ``original_language``. Tied to the origin
  country / where the title was produced; NOT necessarily the spoken language
  (e.g. the anime *Uzumaki* has ``original_language='en'`` because it was an
  Adult Swim production, while it is spoken in Japanese). Used for posters and
  titles, where "the original poster/title" conventionally means the production
  one.

* **content language** — what the title is actually **spoken in**, derived from
  TMDB's ``spoken_languages``. Used for audio and subtitles.

Content-language resolution order:

  1. **Manual override** (a ``babelarr-ov:<lang>`` label) — wins outright,
     including when it is a "no language" marker (``xx`` / ``silent``).
  2. **Only "no language" spoken** (e.g. a silent film: ``spoken=[xx]``) ->
     the special ``NO_LANGUAGE`` ("xx") sentinel. Audio won't match any real
     track (so it's left untouched); subtitle rules can still target it via an
     ``xx:`` / ``silent:`` key, falling to ``default`` otherwise.
  3. **Exactly one** real spoken language -> use it.
  4. **Multiple** real spoken, and the **production** language is among them ->
     use the production language (the authoritative "primary language"; the
     order of spoken_languages is NOT a reliable priority signal).
  5. **Multiple** real spoken, production not among them -> the first real
     spoken language.
  6. **No** spoken languages at all (empty/missing) -> production language.

Note: there is deliberately NO inspection of the file's audio tracks — it made
resolution file-dependent and relied on the unreliable spoken_languages order.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .langcodes import NO_LANGUAGE, is_no_language, normalize


def resolve_content_language(
    production_language: Optional[str],
    spoken_languages: Sequence[Optional[str]],
    override: Optional[str] = None,
) -> Optional[str]:
    """Return the normalized content language, ``NO_LANGUAGE``, or ``None``.

    ``None`` means "undeterminable -> leave audio/subtitles untouched".
    ``NO_LANGUAGE`` ("xx") means "no spoken language" (silent/music-only).
    """
    # 1. Manual override wins (including xx / silent).
    if override and override.strip():
        if is_no_language(override):
            return NO_LANGUAGE
        norm_override = normalize(override)
        if norm_override is not None:
            return norm_override

    raw = [s for s in spoken_languages if s]

    # 2. Spoken list present but entirely "no language" markers -> silent.
    if raw and all(is_no_language(s) for s in raw):
        return NO_LANGUAGE

    # Real (normalizable) spoken languages, in TMDB order, de-duplicated.
    real: list = []
    for s in raw:
        n = normalize(s)
        if n is not None and n not in real:
            real.append(n)

    # 6. No real spoken languages -> production.
    if not real:
        return normalize(production_language)

    # 3. Exactly one real spoken language.
    if len(real) == 1:
        return real[0]

    # 4. Multiple real: prefer production language if it's among them.
    prod = normalize(production_language)
    if prod is not None and prod in real:
        return prod

    # 5. Otherwise the first real spoken language.
    return real[0]
