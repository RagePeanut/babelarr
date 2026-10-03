"""Tests for config loading: config file, env-var override, enforcement."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib.util  # noqa: E402

import pytest  # noqa: E402

from babelarr.config import Config, ConfigError  # noqa: E402

_HAS_YAML = importlib.util.find_spec("yaml") is not None
yaml_only = pytest.mark.skipif(not _HAS_YAML, reason="PyYAML not installed")


@pytest.fixture
def base_env(monkeypatch, tmp_path):
    """Required env vars + a clean slate for all optional rule vars."""
    monkeypatch.setenv("PLEX_URL", "http://x:32400")
    monkeypatch.setenv("PLEX_TOKEN", "tok")
    monkeypatch.setenv("TMDB_API_KEY", "key")
    for v in ("AUDIO_RULES", "SUBTITLES_RULES", "POSTER_RULES", "TITLE_RULES",
              "CONFIG_FILE", "STATE_PERSISTENCE", "STATE_FILE",
              "SKIP_USER_LOCKED", "PLEX_TIMEOUT", "PLEX_RETRIES"):
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


def test_plex_timeout_and_retries_defaults(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    cfg = Config.from_env()
    assert cfg.plex_timeout == 120
    assert cfg.plex_retries == 3


def test_plex_timeout_and_retries_override(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    base_env.setenv("PLEX_TIMEOUT", "300")
    base_env.setenv("PLEX_RETRIES", "5")
    cfg = Config.from_env()
    assert cfg.plex_timeout == 300
    assert cfg.plex_retries == 5


def test_plex_timeout_invalid(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    base_env.setenv("PLEX_TIMEOUT", "0")
    with pytest.raises(ConfigError, match="PLEX_TIMEOUT"):
        Config.from_env()


def test_plex_retries_invalid(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    base_env.setenv("PLEX_RETRIES", "0")
    with pytest.raises(ConfigError, match="PLEX_RETRIES"):
        Config.from_env()


def test_requires_at_least_one_rule(base_env):
    # No config file at default path, no *_RULES -> error.
    with pytest.raises(ConfigError, match="No rules configured"):
        Config.from_env()


def test_inline_env_rules_ok(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    cfg = Config.from_env()
    assert not cfg.audio_rules.is_empty()
    assert cfg.subtitle_rules.is_empty()
    assert cfg.audio_rules.match("ja") == ["original"]


def test_missing_required_var(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    base_env.delenv("PLEX_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="PLEX_TOKEN"):
        Config.from_env()


def test_explicit_config_file_missing_errors(base_env):
    base_env.setenv("CONFIG_FILE", "/does/not/exist.yml")
    base_env.setenv("AUDIO_RULES", "default:original")
    with pytest.raises(ConfigError, match="CONFIG_FILE not found"):
        Config.from_env()


@yaml_only
def test_config_file_all_concerns(base_env, tmp_path):
    cfg_file = tmp_path / "babelarr.yml"
    cfg_file.write_text(
        """
audio:
  - default: [original]
subtitles:
  - eng: [fre]
  - default: [off]
poster:
  - default: [eng, textless]
title:
  - cjk: [eng]
  - default: [original]
""",
        encoding="utf-8",
    )
    base_env.setenv("CONFIG_FILE", str(cfg_file))
    base_env.setenv("STATE_PERSISTENCE", "file")  # required: poster/title in use
    cfg = Config.from_env()
    assert cfg.audio_rules.match("fr") == ["original"]
    # 'fre' normalizes to canonical ISO 639-3 'fra'.
    assert cfg.subtitle_rules.match("eng") == ["fra"]
    assert cfg.poster_rules.match("de") == ["eng", "textless"]
    assert cfg.title_rules.match("ja") == ["eng"]
    assert cfg.state_persistence == "file"
    assert cfg.skip_user_locked == {"poster", "title"}  # default


@yaml_only
def test_env_var_overrides_config_file_section(base_env, tmp_path):
    cfg_file = tmp_path / "babelarr.yml"
    cfg_file.write_text(
        """
audio:
  - default: [original]
title:
  - default: [original]
""",
        encoding="utf-8",
    )
    base_env.setenv("CONFIG_FILE", str(cfg_file))
    base_env.setenv("STATE_PERSISTENCE", "labels")  # title in use
    # Override just the title concern via env var.
    base_env.setenv("TITLE_RULES", "cjk:eng;default:original")
    cfg = Config.from_env()
    # audio still from file:
    assert cfg.audio_rules.match("fr") == ["original"]
    # title overridden by env var:
    assert cfg.title_rules.match("ja") == ["eng"]


@yaml_only
def test_config_file_unknown_key_errors(base_env, tmp_path):
    cfg_file = tmp_path / "babelarr.yml"
    cfg_file.write_text("audios:\n  - default: [original]\n", encoding="utf-8")
    base_env.setenv("CONFIG_FILE", str(cfg_file))
    with pytest.raises(ConfigError, match="Unknown keys"):
        Config.from_env()


@yaml_only
def test_config_file_invalid_token_errors(base_env, tmp_path):
    cfg_file = tmp_path / "babelarr.yml"
    # 'off' is not valid for audio.
    cfg_file.write_text("audio:\n  - default: [off]\n", encoding="utf-8")
    base_env.setenv("CONFIG_FILE", str(cfg_file))
    with pytest.raises(ConfigError, match="audio rules"):
        Config.from_env()


# --- STATE_PERSISTENCE (required only when poster/title rules exist) --------

def test_state_persistence_not_required_without_poster_title(base_env):
    # Audio-only config: state persistence is irrelevant and not required.
    base_env.setenv("AUDIO_RULES", "default:original")
    cfg = Config.from_env()  # must not raise
    assert cfg.state_persistence == ""


def test_state_persistence_required_with_poster_rules(base_env):
    base_env.setenv("POSTER_RULES", "default:eng")
    with pytest.raises(ConfigError, match="STATE_PERSISTENCE is required"):
        Config.from_env()


def test_state_persistence_required_with_title_rules(base_env):
    base_env.setenv("TITLE_RULES", "default:original")
    with pytest.raises(ConfigError, match="STATE_PERSISTENCE is required"):
        Config.from_env()


def test_state_persistence_invalid_value(base_env):
    base_env.setenv("POSTER_RULES", "default:eng")
    base_env.setenv("STATE_PERSISTENCE", "sqlite")
    with pytest.raises(ConfigError, match="must be 'labels' or 'file'"):
        Config.from_env()


def test_state_persistence_file_default_path(base_env):
    base_env.setenv("POSTER_RULES", "default:eng")
    base_env.setenv("STATE_PERSISTENCE", "file")
    cfg = Config.from_env()
    assert cfg.state_persistence == "file"
    assert cfg.state_file == "/config/babelarr-state.json"


def test_state_file_override(base_env):
    base_env.setenv("POSTER_RULES", "default:eng")
    base_env.setenv("STATE_PERSISTENCE", "file")
    base_env.setenv("STATE_FILE", "/data/custom.json")
    cfg = Config.from_env()
    assert cfg.state_file == "/data/custom.json"


# --- SKIP_USER_LOCKED parsing ----------------------------------------------

def test_skip_user_locked_default_is_both(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    cfg = Config.from_env()
    assert cfg.skip_user_locked == {"poster", "title"}


def test_skip_user_locked_true(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    base_env.setenv("SKIP_USER_LOCKED", "true")
    assert Config.from_env().skip_user_locked == {"poster", "title"}


def test_skip_user_locked_false(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    base_env.setenv("SKIP_USER_LOCKED", "false")
    assert Config.from_env().skip_user_locked == set()


def test_skip_user_locked_single_field(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    base_env.setenv("SKIP_USER_LOCKED", "poster")
    assert Config.from_env().skip_user_locked == {"poster"}


def test_skip_user_locked_both_explicit(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    base_env.setenv("SKIP_USER_LOCKED", "poster,title")
    assert Config.from_env().skip_user_locked == {"poster", "title"}


def test_skip_user_locked_unknown_field_errors(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    base_env.setenv("SKIP_USER_LOCKED", "artwork")
    with pytest.raises(ConfigError, match="unknown field"):
        Config.from_env()


def test_skip_user_locked_alias_cannot_combine(base_env):
    base_env.setenv("AUDIO_RULES", "default:original")
    base_env.setenv("SKIP_USER_LOCKED", "true,poster")
    with pytest.raises(ConfigError, match="cannot be combined"):
        Config.from_env()
