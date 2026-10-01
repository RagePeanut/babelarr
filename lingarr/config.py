"""Configuration loading from environment variables.

All config is supplied as flat environment variables to match the rest of the
user's arr stack. The only structured value, ``SUBTITLE_RULES``, accepts either
an inline compact string *or* a path to a YAML file (auto-detected).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .langcodes import normalize


class ConfigError(Exception):
    """Raised when configuration is missing or malformed."""


# Sentinel key for the fallback subtitle rule.
DEFAULT_RULE = "default"


@dataclass
class SubtitleRules:
    """Per-audio-language ordered subtitle preferences.

    ``rules`` maps a normalized audio-language code (639-3) to an ordered list
    of normalized subtitle-language codes. An empty list means "disable
    subtitles for this audio language". ``default`` applies to any audio
    language without an explicit rule.

    An explicit rule whose preferences all miss results in subtitles OFF; it
    never falls through to ``default``.
    """

    rules: Dict[str, List[str]] = field(default_factory=dict)
    default: Optional[List[str]] = None

    def is_empty(self) -> bool:
        return not self.rules and self.default is None

    def lookup(self, audio_lang: Optional[str]) -> Optional[List[str]]:
        """Return the ordered subtitle preferences for an audio language.

        Returns ``None`` only when there is no explicit rule AND no default,
        meaning "leave subtitles untouched". An empty list means "disable".
        """
        norm = normalize(audio_lang)
        if norm is not None and norm in self.rules:
            return self.rules[norm]
        return self.default


def _parse_pref_list(raw: str) -> List[str]:
    out: List[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        norm = normalize(item)
        if norm is None:
            raise ConfigError(f"Unknown subtitle language code in rules: {item!r}")
        out.append(norm)
    return out


def parse_subtitle_rules_inline(value: str) -> SubtitleRules:
    """Parse the compact string form, e.g. ``eng:fre;fre:;default:fre,eng``."""
    rules: Dict[str, List[str]] = {}
    default: Optional[List[str]] = None
    for chunk in value.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ConfigError(
                f"Malformed subtitle rule {chunk!r}; expected 'audio:sub1,sub2'"
            )
        key, _, prefs = chunk.partition(":")
        key = key.strip().lower()
        pref_list = _parse_pref_list(prefs)
        if key == DEFAULT_RULE:
            default = pref_list
        else:
            norm_key = normalize(key)
            if norm_key is None:
                raise ConfigError(f"Unknown audio language code in rules: {key!r}")
            rules[norm_key] = pref_list
    return SubtitleRules(rules=rules, default=default)


def parse_subtitle_rules_yaml(text: str) -> SubtitleRules:
    """Parse the YAML file form.

    Expected shape::

        rules:
          eng: [fre]
          fre: []
          default: [fre, eng]
    """
    import yaml  # imported lazily so inline-only usage needs no PyYAML

    data = yaml.safe_load(text) or {}
    raw_rules = data.get("rules", data)  # allow top-level map too
    if not isinstance(raw_rules, dict):
        raise ConfigError("subtitle rules YAML must be a mapping")

    rules: Dict[str, List[str]] = {}
    default: Optional[List[str]] = None
    for key, prefs in raw_rules.items():
        key = str(key).strip().lower()
        if prefs is None:
            pref_list: List[str] = []
        elif isinstance(prefs, (list, tuple)):
            pref_list = []
            for p in prefs:
                norm = normalize(str(p))
                if norm is None:
                    raise ConfigError(f"Unknown subtitle language code: {p!r}")
                pref_list.append(norm)
        else:
            raise ConfigError(f"Subtitle preferences for {key!r} must be a list")

        if key == DEFAULT_RULE:
            default = pref_list
        else:
            norm_key = normalize(key)
            if norm_key is None:
                raise ConfigError(f"Unknown audio language code: {key!r}")
            rules[norm_key] = pref_list
    return SubtitleRules(rules=rules, default=default)


def load_subtitle_rules(value: Optional[str]) -> SubtitleRules:
    """Load rules from the ``SUBTITLE_RULES`` value (file path or inline)."""
    if not value or not value.strip():
        return SubtitleRules()  # empty -> leave subtitles untouched
    value = value.strip()
    maybe_path = Path(value)
    if maybe_path.exists() and maybe_path.is_file():
        return parse_subtitle_rules_yaml(maybe_path.read_text(encoding="utf-8"))
    return parse_subtitle_rules_inline(value)


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
    subtitle_rules: SubtitleRules
    max_audio_channels: Optional[int]
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
            subtitle_rules=load_subtitle_rules(os.environ.get("SUBTITLE_RULES")),
            max_audio_channels=max_channels,
            sweep_interval_minutes=_env_int("SWEEP_INTERVAL_MINUTES", 360),
            new_media_mode=mode,
            webhook_port=_env_int("WEBHOOK_PORT", 9999),
            recent_poll_interval_minutes=_env_int(
                "RECENT_POLL_INTERVAL_MINUTES", 15
            ),
            dry_run=_env_bool("DRY_RUN", False),
        )
