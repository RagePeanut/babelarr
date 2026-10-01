"""Configuration loading from environment variables.

All config is supplied as flat environment variables to match the rest of the
user's *arr stack. Babelarr drives three independent, opt-in concerns off each
title's TMDB original language:

  * ``SUBTITLE_RULES`` — which subtitle track to enable (or force OFF).
  * ``POSTER_RULES``   — which language's poster (or a textless one) to set.
  * ``TITLE_RULES``    — which language's title (or the original) to set.

Each of these accepts either a compact inline string or a path to a YAML file
(auto-detected). All share the same ordered, top-to-bottom, first-match rule
grammar (see ``rules.py``). Any rule set left unset means "leave that concern
completely untouched".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .rules import (
    RuleError,
    RuleSet,
    TOKEN_OFF,
    TOKEN_ORIGINAL,
    TOKEN_TEXTLESS,
    parse_inline,
    parse_yaml,
)


class ConfigError(Exception):
    """Raised when configuration is missing or malformed."""


# Which reserved preference tokens each concern permits.
SUBTITLE_TOKENS = {TOKEN_ORIGINAL, TOKEN_OFF}
POSTER_TOKENS = {TOKEN_TEXTLESS}
TITLE_TOKENS = {TOKEN_ORIGINAL}


def _load_rules(value: Optional[str], allowed_tokens: set) -> RuleSet:
    """Load a RuleSet from an env value: a YAML file path or an inline string."""
    if not value or not value.strip():
        return RuleSet()  # empty -> leave this concern untouched
    value = value.strip()
    maybe_path = Path(value)
    try:
        if maybe_path.exists() and maybe_path.is_file():
            return parse_yaml(
                maybe_path.read_text(encoding="utf-8"), allowed_tokens
            )
        return parse_inline(value, allowed_tokens)
    except RuleError as exc:
        raise ConfigError(str(exc)) from exc


def _env_int(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    plex_url: str
    plex_token: str
    tmdb_api_key: str
    libraries: Optional[List[str]]  # None -> all movie+show libraries
    subtitle_rules: RuleSet
    poster_rules: RuleSet
    title_rules: RuleSet
    max_audio_channels: Optional[int]
    only_replace_unlocked: bool
    sweep_interval_minutes: int
    new_media_mode: str  # webhook | polling | disabled
    webhook_port: int
    recent_poll_interval_minutes: int
    dry_run: bool

    @classmethod
    def from_env(cls) -> "Config":
        plex_url = os.environ.get("PLEX_URL", "").strip()
        plex_token = os.environ.get("PLEX_TOKEN", "").strip()
        tmdb_api_key = os.environ.get("TMDB_API_KEY", "").strip()

        missing = [
            n
            for n, v in (
                ("PLEX_URL", plex_url),
                ("PLEX_TOKEN", plex_token),
                ("TMDB_API_KEY", tmdb_api_key),
            )
            if not v
        ]
        if missing:
            raise ConfigError(f"Missing required env vars: {', '.join(missing)}")

        libs_raw = os.environ.get("PLEX_LIBRARIES", "").strip()
        libraries = (
            [s.strip() for s in libs_raw.split(",") if s.strip()] if libs_raw else None
        )

        mode = os.environ.get("NEW_MEDIA_MODE", "disabled").strip().lower()
        if mode not in ("webhook", "polling", "disabled"):
            raise ConfigError(
                f"NEW_MEDIA_MODE must be webhook|polling|disabled, got {mode!r}"
            )

        max_channels = _env_int("MAX_AUDIO_CHANNELS", None)
        if max_channels is not None and max_channels < 1:
            raise ConfigError("MAX_AUDIO_CHANNELS must be >= 1 when set")

        return cls(
            plex_url=plex_url,
            plex_token=plex_token,
            tmdb_api_key=tmdb_api_key,
            libraries=libraries,
            subtitle_rules=_load_rules(
                os.environ.get("SUBTITLE_RULES"), SUBTITLE_TOKENS
            ),
            poster_rules=_load_rules(os.environ.get("POSTER_RULES"), POSTER_TOKENS),
            title_rules=_load_rules(os.environ.get("TITLE_RULES"), TITLE_TOKENS),
            max_audio_channels=max_channels,
            only_replace_unlocked=_env_bool("ONLY_REPLACE_UNLOCKED", True),
            sweep_interval_minutes=_env_int("SWEEP_INTERVAL_MINUTES", 360),
            new_media_mode=mode,
            webhook_port=_env_int("WEBHOOK_PORT", 9999),
            recent_poll_interval_minutes=_env_int(
                "RECENT_POLL_INTERVAL_MINUTES", 15
            ),
            dry_run=_env_bool("DRY_RUN", False),
        )
