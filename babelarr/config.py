"""Configuration loading.

Babelarr drives four independent, opt-in concerns off each title's TMDB
original language:

  * ``audio``     — which audio track (language) to default to.
  * ``subtitles`` — which subtitle track to enable (or force OFF).
  * ``poster``    — which language's poster (or a textless one) to set.
  * ``title``     — which language's title (or the original) to set.

Rules can be supplied two ways:

  1. A unified **config file** (YAML) with top-level keys ``audio``,
     ``subtitles``, ``poster`` and ``title``, pointed at by ``CONFIG_FILE``
     (default ``/config/babelarr.yml`` if present).
  2. Per-concern **environment variables** ``AUDIO_RULES``, ``SUBTITLE_RULES``,
     ``POSTER_RULES``, ``TITLE_RULES`` — each an inline string or a path to a
     standalone YAML file.

**Precedence:** an environment variable, when set, **overrides** that concern's
section in the config file. Each concern is independent; a concern with no rules
from either source is simply left untouched. At least one concern must be
configured (from either source), or startup fails.
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
    parse_rules_structure,
    parse_yaml,
)


class ConfigError(Exception):
    """Raised when configuration is missing or malformed."""


# Which reserved preference tokens each concern permits.
AUDIO_TOKENS = {TOKEN_ORIGINAL}
SUBTITLE_TOKENS = {TOKEN_ORIGINAL, TOKEN_OFF}
POSTER_TOKENS = {TOKEN_TEXTLESS}
TITLE_TOKENS = {TOKEN_ORIGINAL}

DEFAULT_CONFIG_PATH = "/config/babelarr.yml"

# concern name -> (env var, config-file key, allowed tokens)
_CONCERNS = {
    "audio": ("AUDIO_RULES", "audio", AUDIO_TOKENS),
    "subtitles": ("SUBTITLE_RULES", "subtitles", SUBTITLE_TOKENS),
    "poster": ("POSTER_RULES", "poster", POSTER_TOKENS),
    "title": ("TITLE_RULES", "title", TITLE_TOKENS),
}


def _rules_from_env_value(value: str, allowed_tokens: set, concern: str) -> RuleSet:
    """A single ``*_RULES`` env value: a YAML file path or an inline string."""
    value = value.strip()
    maybe_path = Path(value)
    try:
        if maybe_path.exists() and maybe_path.is_file():
            return parse_yaml(maybe_path.read_text(encoding="utf-8"), allowed_tokens)
        return parse_inline(value, allowed_tokens)
    except RuleError as exc:
        raise ConfigError(f"{concern} rules: {exc}") from exc


def _load_config_file() -> dict:
    """Load the unified config file if present. Returns its concern sections."""
    path = os.environ.get("CONFIG_FILE", "").strip() or DEFAULT_CONFIG_PATH
    p = Path(path)
    if not p.exists() or not p.is_file():
        # An explicitly-set CONFIG_FILE that is missing is an error; the default
        # path simply being absent is fine (env vars may provide everything).
        if os.environ.get("CONFIG_FILE", "").strip():
            raise ConfigError(f"CONFIG_FILE not found: {path}")
        return {}
    import yaml  # lazy

    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - passthrough
        raise ConfigError(f"Could not parse config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Config file must be a mapping of concern -> rules")
    unknown = set(data) - set(_CONCERNS)
    if unknown:
        raise ConfigError(
            f"Unknown keys in config file: {', '.join(sorted(unknown))}. "
            f"Valid keys: {', '.join(_CONCERNS)}"
        )
    return data


def _resolve_rules(file_sections: dict) -> dict:
    """Resolve each concern's RuleSet, env var overriding the config file."""
    resolved: dict = {}
    for concern, (env_var, file_key, tokens) in _CONCERNS.items():
        env_val = os.environ.get(env_var)
        if env_val is not None and env_val.strip():
            # Env var present -> overrides the config file for this concern.
            resolved[concern] = _rules_from_env_value(env_val, tokens, concern)
        elif file_key in file_sections:
            try:
                resolved[concern] = parse_rules_structure(
                    file_sections[file_key], tokens
                )
            except RuleError as exc:
                raise ConfigError(f"{concern} rules (config file): {exc}") from exc
        else:
            resolved[concern] = RuleSet()  # untouched
    return resolved


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
    audio_rules: RuleSet
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

        file_sections = _load_config_file()
        rules = _resolve_rules(file_sections)

        if all(rs.is_empty() for rs in rules.values()):
            raise ConfigError(
                "No rules configured. Set at least one of AUDIO_RULES, "
                "SUBTITLE_RULES, POSTER_RULES, TITLE_RULES, or provide a "
                "config file (CONFIG_FILE / /config/babelarr.yml) with at "
                "least one of: audio, subtitles, poster, title."
            )

        return cls(
            plex_url=plex_url,
            plex_token=plex_token,
            tmdb_api_key=tmdb_api_key,
            libraries=libraries,
            audio_rules=rules["audio"],
            subtitle_rules=rules["subtitles"],
            poster_rules=rules["poster"],
            title_rules=rules["title"],
            max_audio_channels=max_channels,
            only_replace_unlocked=_env_bool("ONLY_REPLACE_UNLOCKED", True),
            sweep_interval_minutes=_env_int("SWEEP_INTERVAL_MINUTES", 360),
            new_media_mode=mode,
            webhook_port=_env_int("WEBHOOK_PORT", 9999),
            recent_poll_interval_minutes=_env_int("RECENT_POLL_INTERVAL_MINUTES", 15),
            dry_run=_env_bool("DRY_RUN", False),
        )
