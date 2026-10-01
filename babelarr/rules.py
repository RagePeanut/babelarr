"""Unified, ordered rule engine shared by subtitles, posters, and titles.

A *rule set* is an **ordered** list of ``(key, preferences)`` entries. At
runtime we walk the list **top to bottom** and the **first matching key wins** —
that matched rule is authoritative (we never fall through to a later rule, not
even ``default``). This single evaluation model is used identically for all
three concerns; only the set of legal preference *tokens* differs.

Keys may be:
  * a concrete original language (``fre``), normalized via ``langcodes``;
  * a script class (``cjk``, ``kana``, ``cyrillic`` ...), see ``langscript``;
  * ``default`` — matches any original language. Only valid as the LAST entry.

Preferences are an ordered list; the first one that is actually available wins.
Besides concrete language codes, these reserved tokens are accepted (per
concern, enforced by the caller via ``allowed_tokens``):
  * ``original``  — subtitles: original-language track; titles: TMDB original title.
  * ``off``       — subtitles: force OFF.
  * ``textless``  — posters: TMDB "no language / not specified" poster.

Because evaluation is strictly top-to-bottom, declaration order is significant
and easy to get subtly wrong. ``validate_order`` rejects rule sets where an
entry can never be reached because an earlier, broader key already covers it
(e.g. ``cjk`` placed above ``kana``), duplicate keys, or ``default`` not last.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .langcodes import normalize
from .langscript import (
    class_covers_class,
    class_covers_language,
    is_script_class,
)

DEFAULT_KEY = "default"

# Reserved preference tokens (lower-cased).
TOKEN_ORIGINAL = "original"
TOKEN_OFF = "off"
TOKEN_TEXTLESS = "textless"
RESERVED_TOKENS = {TOKEN_ORIGINAL, TOKEN_OFF, TOKEN_TEXTLESS}


class RuleError(Exception):
    """Raised when a rule set is malformed or has an inconsistent order."""


# Kinds of key, used by the validator's "covers" logic.
_KEY_LANGUAGE = "language"
_KEY_SCRIPT = "script"
_KEY_DEFAULT = "default"


@dataclass(frozen=True)
class RuleKey:
    """A normalized rule key plus its kind (language / script / default)."""

    raw: str
    kind: str
    value: str  # normalized language code, lower-cased script class, or "default"

    def covers_language(self, language_code: Optional[str]) -> bool:
        if self.kind == _KEY_DEFAULT:
            return True
        if self.kind == _KEY_SCRIPT:
            return class_covers_language(self.value, language_code)
        return normalize(language_code) == self.value

    def covers_key(self, other: "RuleKey") -> bool:
        """True if this (earlier) key makes ``other`` unreachable."""
        if self.kind == _KEY_DEFAULT:
            return True
        if other.kind == _KEY_DEFAULT:
            return False  # default is broadest; nothing but default covers it
        if self.kind == _KEY_SCRIPT and other.kind == _KEY_SCRIPT:
            return class_covers_class(self.value, other.value)
        if self.kind == _KEY_SCRIPT and other.kind == _KEY_LANGUAGE:
            return class_covers_language(self.value, other.value)
        if self.kind == _KEY_LANGUAGE and other.kind == _KEY_SCRIPT:
            return False  # a single language never covers a whole script class
        # both language
        return self.value == other.value


@dataclass
class Rule:
    key: RuleKey
    preferences: List[str]  # ordered; concrete codes and/or reserved tokens


@dataclass
class RuleSet:
    """An ordered list of rules. Empty => leave this concern untouched."""

    rules: List[Rule] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.rules

    def match(self, original_language: Optional[str]) -> Optional[List[str]]:
        """Return the preference list of the first matching rule, else ``None``.

        ``None`` means no rule matched (and no ``default``) -> leave untouched.
        """
        for rule in self.rules:
            if rule.key.covers_language(original_language):
                return rule.preferences
        return None


def _parse_key(raw: str) -> RuleKey:
    key = raw.strip().lower()
    if not key:
        raise RuleError("Empty rule key")
    if key == DEFAULT_KEY:
        return RuleKey(raw=raw, kind=_KEY_DEFAULT, value=DEFAULT_KEY)
    if is_script_class(key):
        return RuleKey(raw=raw, kind=_KEY_SCRIPT, value=key)
    norm = normalize(key)
    if norm is None:
        raise RuleError(
            f"Unknown rule key {raw!r}: not a language code, script class, "
            f"or 'default'"
        )
    return RuleKey(raw=raw, kind=_KEY_LANGUAGE, value=norm)


def _parse_preferences(raw: str, allowed_tokens: set) -> List[str]:
    out: List[str] = []
    for item in raw.split(","):
        item = item.strip().lower()
        if not item:
            continue
        if item in RESERVED_TOKENS:
            if item not in allowed_tokens:
                raise RuleError(
                    f"Token {item!r} is not valid for this rule type "
                    f"(allowed: {', '.join(sorted(allowed_tokens)) or 'none'})"
                )
            out.append(item)
            continue
        norm = normalize(item)
        if norm is None:
            raise RuleError(f"Unknown language code in preferences: {item!r}")
        out.append(norm)
    return out


def validate_order(rules: List[Rule]) -> None:
    """Reject rule sets whose declaration order is inconsistent/ambiguous.

    Errors:
      * duplicate keys (same normalized language / script class / default);
      * ``default`` appearing anywhere but the final position;
      * an entry that is unreachable because an *earlier* key already covers it
        (e.g. ``cjk`` before ``kana``, or ``kana`` before ``jpn``).
    """
    seen: List[RuleKey] = []
    for idx, rule in enumerate(rules):
        key = rule.key

        # default must be last
        if key.kind == _KEY_DEFAULT and idx != len(rules) - 1:
            raise RuleError(
                "'default' must be the last rule; rules after it are unreachable"
            )

        for earlier in seen:
            # exact duplicate
            if earlier.kind == key.kind and earlier.value == key.value:
                raise RuleError(
                    f"Duplicate rule key {key.raw!r} (already declared as "
                    f"{earlier.raw!r})"
                )
            # earlier broader key shadows this one
            if earlier.covers_key(key):
                raise RuleError(
                    f"Unreachable rule {key.raw!r}: an earlier rule "
                    f"{earlier.raw!r} already matches everything it would. "
                    f"Put the more specific rule first."
                )
        seen.append(key)


def parse_rules(entries: List[Tuple[str, str]], allowed_tokens: set) -> RuleSet:
    """Build and validate a RuleSet from ordered ``(key, prefs)`` string pairs."""
    rules: List[Rule] = []
    for raw_key, raw_prefs in entries:
        key = _parse_key(raw_key)
        prefs = _parse_preferences(raw_prefs, allowed_tokens)
        rules.append(Rule(key=key, preferences=prefs))
    validate_order(rules)
    return RuleSet(rules=rules)


def parse_inline(value: str, allowed_tokens: set) -> RuleSet:
    """Parse the compact string form, e.g. ``eng:fre;cjk:en;default:original``.

    Order of ``;``-separated chunks is preserved and significant.
    """
    entries: List[Tuple[str, str]] = []
    for chunk in value.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise RuleError(
                f"Malformed rule {chunk!r}; expected 'key:pref1,pref2'"
            )
        key, _, prefs = chunk.partition(":")
        entries.append((key, prefs))
    return parse_rules(entries, allowed_tokens)


def parse_rules_structure(raw, allowed_tokens: set) -> RuleSet:
    """Build a RuleSet from an already-parsed YAML/JSON structure.

    ``raw`` is a LIST of single-key mappings (order explicit and unambiguous)::

        - eng: [fre]
        - cjk: [en]
        - default: [original]

    A plain mapping is also accepted, relying on insertion-order preservation.
    """
    entries: List[Tuple[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or len(item) != 1:
                raise RuleError(
                    "Each rules list item must be a single 'key: [prefs]' mapping"
                )
            (k, v), = item.items()
            entries.append((_coerce_scalar(k), _prefs_to_str(v)))
    elif isinstance(raw, dict):
        for k, v in raw.items():
            entries.append((_coerce_scalar(k), _prefs_to_str(v)))
    else:
        raise RuleError("rules must be a list or mapping")

    return parse_rules(entries, allowed_tokens)


def parse_yaml(text: str, allowed_tokens: set) -> RuleSet:
    """Parse a standalone per-concern YAML file (optionally wrapped in ``rules:``)."""
    import yaml  # lazy import

    data = yaml.safe_load(text) or {}
    raw = data.get("rules", data) if isinstance(data, dict) else data
    return parse_rules_structure(raw, allowed_tokens)


# YAML 1.1 treats several bare words as booleans (off/no/false -> False,
# on/yes/true -> True). Our reserved token ``off`` and some language-ish words
# would therefore arrive as Python bools after yaml.safe_load. Map them back to
# the string the user clearly wrote so e.g. ``- default: [off]`` works.
_YAML_BOOL_TO_WORD = {False: "off", True: "on"}


def _coerce_scalar(v) -> str:
    """Stringify a YAML scalar, restoring bools that were really bare words."""
    if isinstance(v, bool):
        return _YAML_BOOL_TO_WORD[v]
    return str(v)


def _prefs_to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ",".join(_coerce_scalar(x) for x in v)
    return _coerce_scalar(v)
