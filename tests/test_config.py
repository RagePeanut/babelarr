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
    for v in ("AUDIO_RULES", "SUBTITLE_RULES", "POSTER_RULES", "TITLE_RULES",
              "CONFIG_FILE"):
        monkeypatch.delenv(v, raising=False)
    # Point the default config path somewhere guaranteed absent.
    monkeypatch.setenv("CONFIG_FILE", str(tmp_path / "nope.yml"))
    monkeypatch.delenv("CONFIG_FILE", raising=False)
    return monkeypatch


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
    cfg = Config.from_env()
    assert cfg.audio_rules.match("fr") == ["original"]
    assert cfg.subtitle_rules.match("eng") == ["fre"]
    assert cfg.poster_rules.match("de") == ["eng", "textless"]
    assert cfg.title_rules.match("ja") == ["eng"]


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
