"""Tests for subtitle-format alias resolution (``SUBTITLE_CODEC_PRIORITY``)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from babelarr.subformats import canonical_names, match_set  # noqa: E402


def test_canonical_includes_itself():
    assert "srt" in match_set("srt")


def test_srt_matches_subrip():
    s = match_set("srt")
    assert "srt" in s and "subrip" in s


def test_ass_is_canonical_and_matches_ssa():
    assert "ass" in canonical_names()
    s = match_set("ass")
    assert "ass" in s and "ssa" in s


def test_ssa_entry_is_literal_only():
    # ssa is an alias of ass, not its own canonical -> matches only itself.
    assert match_set("ssa") == frozenset({"ssa"})


def test_pgs_matches_hdmv():
    assert "hdmv_pgs_subtitle" in match_set("pgs")


def test_vobsub_matches_dvd_subtitle():
    assert "dvd_subtitle" in match_set("vobsub")


def test_case_insensitive_entry():
    assert "subrip" in match_set("SRT")


def test_unmapped_value_matches_only_itself():
    assert match_set("weirdsub") == frozenset({"weirdsub"})


def test_canonical_names_nonempty():
    names = canonical_names()
    assert {"srt", "pgs", "vobsub"} <= names
