"""Per-field lock-state persistence.

Babelarr locks the Plex fields it sets (``title`` / ``thumb`` / ``clearLogo`` /
``art``) so Plex's agent won't revert them. But a locked field alone can't tell
us *who* locked it — us,
or the user hand-picking a value. To respect the user's manual choices
(``SKIP_USER_LOCKED``) while still re-managing our own values when rules change,
we record a **fingerprint** of the value we last wrote for each field, and on
every pass compare it to the field's current value:

* current value's fingerprint == the one we recorded  -> the value is **ours**
  and untouched; we may update it (e.g. after a rule change).
* fingerprint missing or different                    -> the field is **user
  owned** (they changed it, or we never set it); honor ``SKIP_USER_LOCKED``.

The fingerprint is a short SHA-256 of the exact value written. Two
interchangeable persistence mechanisms are offered, chosen via
``STATE_PERSISTENCE`` (required, no default — the choice is impactful):

* ``labels`` — stored as Plex labels ``babelarr-locked:<field>:<hash>`` on the
  item. All state lives in Plex (nothing external), but the labels are visible
  and editable in the Plex UI: if a user edits/removes one, that field looks
  user-owned and Babelarr stops managing it (a safe, if sticky, failure).
* ``file``   — stored in a JSON file (``STATE_FILE``), keyed by the item's TMDB/
  IMDB guid (surviving library rebuilds), falling back to ratingKey. Invisible
  to Plex. If the file is lost/corrupt, every field looks user-owned and
  Babelarr backs off (never clobbers).

Both mechanisms fail toward **"don't clobber"**.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Optional, Protocol

log = logging.getLogger("babelarr.state")

LABEL_PREFIX = "babelarr-locked"
_HASH_LEN = 8


def fingerprint(value: str) -> str:
    """Short, stable fingerprint of a written field value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_HASH_LEN]


def label_for(field: str, value: str) -> str:
    """The bookkeeping label representing 'Babelarr wrote <value> to <field>'."""
    return f"{LABEL_PREFIX}:{field}:{fingerprint(value)}"


def label_prefix_for(field: str) -> str:
    """Prefix matching any Babelarr lock label for a field (for self-healing)."""
    return f"{LABEL_PREFIX}:{field}:"


class StatePersistence(Protocol):
    """Interface the processor/plex layer uses, regardless of mechanism."""

    def is_ours(self, item, field: str, current_value: Optional[str]) -> bool:
        """True if ``current_value`` matches the fingerprint we recorded."""

    def record(self, item, field: str, value: str, extra_tags=None) -> None:
        """Persist that Babelarr wrote ``value`` to ``field`` on ``item``.

        ``extra_tags`` is an optional iterable of label tag strings known to be
        on the server but possibly absent from ``item.labels`` (e.g. after a
        reload races Plex's eventual-consistency on label writes). The labels
        backend folds them into its stale-label self-heal so prior lock labels
        are reliably removed; other backends ignore it.
        """

    def clear(self, item, field: str, extra_tags=None) -> None:
        """Remove any recorded value for ``field`` on ``item`` (if present)."""

    def flush(self) -> None:
        """Persist any buffered state (no-op for label backend)."""


# -- labels backend ----------------------------------------------------------

def _item_label_tags(item) -> list:
    return [getattr(l, "tag", None) for l in getattr(item, "labels", []) or []]


class LabelState:
    """Persist fingerprints as Plex labels on the item."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

    def is_ours(self, item, field: str, current_value: Optional[str]) -> bool:
        if current_value is None:
            return False
        # Plex may re-case stored labels (it title-cases them, e.g.
        # "babelarr-locked:title:..." -> "Babelarr-locked:title:..."), so
        # compare case-insensitively.
        want = label_for(field, current_value).lower()
        return any(t.lower() == want for t in _item_label_tags(item) if t)

    def record(self, item, field: str, value: str, extra_tags=None) -> None:
        new_label = label_for(field, value)
        new_lc = new_label.lower()
        prefix_lc = label_prefix_for(field).lower()
        existing = self._known_tags(item, extra_tags)
        # Case-insensitive matching against what Plex actually stored.
        already_present = any(t.lower() == new_lc for t in existing)
        stale = [
            t for t in existing
            if t.lower().startswith(prefix_lc) and t.lower() != new_lc
        ]
        if self.dry_run:
            return
        # Self-heal: drop any prior/duplicate lock labels for this field, keep one.
        if stale:
            item.removeLabel(stale, locked=False)
        if not already_present:
            item.addLabel([new_label], locked=False)

    def clear(self, item, field: str, extra_tags=None) -> None:
        prefix_lc = label_prefix_for(field).lower()
        stale = [t for t in self._known_tags(item, extra_tags)
                 if t.lower().startswith(prefix_lc)]
        if self.dry_run or not stale:
            return
        item.removeLabel(stale, locked=False)

    @staticmethod
    def _known_tags(item, extra_tags=None) -> list:
        """Union of the item's current label tags and any ``extra_tags``.

        ``extra_tags`` carries labels known to exist on the server but possibly
        missing from a freshly-reloaded ``item.labels`` (Plex is
        eventually-consistent on label writes). Folding them in lets the
        stale-label self-heal remove prior lock labels reliably. Deduplicated
        case-insensitively, preserving order.
        """
        out = []
        seen = set()
        for t in list(_item_label_tags(item)) + list(extra_tags or []):
            if not t:
                continue
            lc = t.lower()
            if lc not in seen:
                seen.add(lc)
                out.append(t)
        return out

    def flush(self) -> None:  # nothing buffered
        return


# -- file backend ------------------------------------------------------------

def item_guid_key(item) -> str:
    """Stable key for an item: its TMDB/IMDB/TVDB guid, else ratingKey."""
    import re

    guids = []
    for g in getattr(item, "guids", []) or []:
        gid = getattr(g, "id", None)
        if gid:
            guids.append(gid)
    legacy = getattr(item, "guid", None)
    if legacy:
        guids.append(legacy)
    for pattern in (r"tmdb://\d+", r"imdb://tt\d+", r"tvdb://\d+"):
        for g in guids:
            m = re.search(pattern, g)
            if m:
                return m.group(0)
    return f"ratingKey://{getattr(item, 'ratingKey', '?')}"


class FileState:
    """Persist fingerprints in a JSON file keyed by item guid."""

    def __init__(self, path: str, dry_run: bool = False):
        self.path = Path(path)
        self.dry_run = dry_run
        self._lock = threading.Lock()
        self._dirty = False
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with self.path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
            log.warning("State file %s is not an object; ignoring", self.path)
        except (OSError, ValueError) as exc:
            # Fail safe: forget state -> everything looks user-owned -> back off.
            log.warning("Could not read state file %s (%s); starting empty",
                        self.path, exc)
        return {}

    def is_ours(self, item, field: str, current_value: Optional[str]) -> bool:
        if current_value is None:
            return False
        key = item_guid_key(item)
        with self._lock:
            recorded = self._data.get(key, {}).get(field)
        return recorded is not None and recorded == fingerprint(current_value)

    def record(self, item, field: str, value: str, extra_tags=None) -> None:
        # extra_tags is a label-backend concern (eventual-consistency healing);
        # the file backend keys off guids, so it is irrelevant here.
        key = item_guid_key(item)
        with self._lock:
            self._data.setdefault(key, {})[field] = fingerprint(value)
            self._dirty = True

    def clear(self, item, field: str, extra_tags=None) -> None:
        key = item_guid_key(item)
        with self._lock:
            entry = self._data.get(key)
            if entry and field in entry:
                del entry[field]
                if not entry:
                    self._data.pop(key, None)
                self._dirty = True

    def flush(self) -> None:
        if self.dry_run:
            return
        with self._lock:
            if not self._dirty:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                with tmp.open("w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, indent=2, sort_keys=True)
                tmp.replace(self.path)
                self._dirty = False
            except OSError as exc:  # pragma: no cover - defensive
                log.warning("Could not write state file %s: %s", self.path, exc)


def build_state(persistence: str, state_file: str, dry_run: bool) -> StatePersistence:
    """Factory: construct the chosen persistence backend."""
    if persistence == "labels":
        return LabelState(dry_run=dry_run)
    if persistence == "file":
        return FileState(state_file, dry_run=dry_run)
    raise ValueError(f"Unknown state persistence: {persistence!r}")
