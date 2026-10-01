"""Babelarr - make Plex speak your languages.

For every movie and show, Babelarr looks up the title's original language from
TMDB and, driven by independent per-language rule sets, can:
  * set the default audio + subtitle tracks,
  * set the poster to a chosen language (or a textless one),
  * set the display title to a chosen language (or the original).

Each concern is opt-in; an unset rule set leaves it completely untouched.
"""

__version__ = "0.2.0"
