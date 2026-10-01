# Babelarr

**Make Plex speak your languages.**

Plex isn't built for polyglots — Babelarr fixes that. For every movie and show,
it looks up the title's **original language** from TMDB and, driven by four
independent sets of per-language rules, can:

1. Set the default **audio** track to a chosen language — your original-language
   ("OV") purists and your native-dub watchers are both first-class.
2. Set the **subtitle** track according to your rules (or force it off).
3. Set the **poster** to a chosen language — or a textless one.
4. Set the **display title** to a chosen language — or the original.

Within the chosen audio language the best track is picked automatically (most
channels, with an optional ceiling; codec quality breaks ties).

Each concern is **opt-in**: leave its rule set unset and Babelarr won't touch
it. It runs as a single Docker container that fits a typical \*arr stack. A full
library sweep always runs on a schedule; optionally, new media is handled
near-instantly via a Plex **webhook** (Plex Pass) or by **polling** recently
added items (no Plex Pass).

> Plex runs on the host (e.g. your NAS) — Babelarr talks to it over the network
> via `PLEX_URL`. Plex does **not** need to be in the same compose stack.

---

## The rule model (read this first)

All four concerns — audio, subtitles, posters, titles — share **one** rule
grammar.

A rule set is an **ordered list** of `key: preferences` entries. At runtime
Babelarr walks the list **top to bottom** and the **first matching key wins**.
That matched rule is **authoritative**: Babelarr never falls through to a later
rule — not even `default` — once a key has matched.

```
key : pref1, pref2, pref3
```

* **key** — matched against the title's **original language**. It may be:
  * a concrete language code (`fre`, `ja`, `de`, …), any ISO 639 form;
  * a **script class** (see below) — `cjk`, `kana`, `cyrillic`, …;
  * `default` — matches anything. **Only valid as the last entry.**
* **preferences** — an ordered list; the **first available one wins**. Besides
  language codes, each concern allows certain reserved tokens (below).

**Order matters.** Because evaluation is strictly top-to-bottom and the first
match is final, the sequence you write your rules in is significant. Put the
**most specific** keys first and broader ones (script classes, then `default`)
last.

### What happens on a miss

* **No key matches** (and there's no `default`) → that concern is **left
  untouched**.
* **A key matches but none of its preferences are available** → **left
  untouched** too. It does **not** fall through to `default`.
  (The one exception is the subtitle `off` token, which always applies.)

### Reserved preference tokens

These are the only reserved values, and they are **values only** — never keys.

| Token | Audio | Subtitles | Posters | Titles |
|-------|-------|-----------|---------|--------|
| `original` | the original-language audio track | — | the original-language poster | TMDB **original title** |
| `off` | — | force subtitles **OFF** (distinct from "untouched") | — | — |
| `textless` | — | — | TMDB **"no language"** poster¹ | — |

Notes on the gaps:
* **Audio** has no `off` — a video always plays *some* audio, so audio is never
  "disabled".
* **Subtitles** have no `original` — subtitle rules key on the audio language
  that actually plays, so concrete language keys plus `default` already cover
  every case; an `original` token would be redundant.

¹ `textless` maps to TMDB's "no language / not specified" image category.
Because TMDB is community-maintained, these posters are **not guaranteed** to be
free of title text — they're only *categorized* that way, and miscategorization
happens. Treat it as best-effort.

### Script classes

Keys can match the **writing system** of the original language, so you can make
coarse decisions without listing every language. `cjk` is an umbrella over three
leaves:

```
cjk ─┬─ han     (Chinese)
     ├─ kana    (Japanese)
     └─ hangul  (Korean)
```

Other (flat) classes: `latin`, `cyrillic`, `greek`, `arabic`, `hebrew`,
`devanagari`, `thai`, `armenian`, `georgian`.

### Ordering is validated — Babelarr refuses to start on an inconsistent set

Because order decides the outcome, Babelarr **errors out at startup** (rather
than silently guessing) if a rule set is ambiguous or has unreachable rules:

* **Duplicate key** — the same language / script class / `default` twice
  (including the same language written in different ISO forms, e.g. `fr` and
  `fra`).
* **`default` not last** — any rule after `default` can never run.
* **A rule shadowed by an earlier, broader key** — e.g. `cjk` before `kana`
  (the `cjk` rule already caught every Japanese title), or `kana` before `jpn`.
  The fix is always to **put the more specific rule first**.

Examples:

```
# ❌ error: 'kana' is unreachable — 'cjk' above already matched all Japanese
cjk:eng;kana:fra

# ✅ specific first, broader after — every rule can still fire
jpn:original;cjk:eng;default:original

# ✅ siblings never overlap, so order between them is free
kana:eng;han:fra;default:original
```

Language codes may be written in any ISO 639 form — `fr`, `fre`, and `fra` are
all understood and compared equivalently.

---

## Where rules come from (config file vs. env vars)

Rules for the four concerns can be supplied two ways, and you can mix them:

### 1. A unified config file (recommended)

One YAML file with top-level keys `audio`, `subtitles`, `poster`, `title`, each
an ordered list of rules. Mount it and set `CONFIG_FILE=/config/babelarr.yml`
(that path is also the **default**, so you can omit `CONFIG_FILE` if you mount
there). This is the cleanest way to manage all four concerns together:

```yaml
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
```

See [`config/babelarr.example.yml`](config/babelarr.example.yml). Omit a concern
to leave it untouched.

### 2. Per-concern environment variables

`AUDIO_RULES`, `SUBTITLES_RULES`, `POSTER_RULES`, `TITLE_RULES`. Each accepts
either form (auto-detected):

* **Inline string** — entries separated by `;`, preferences by `,`:
  ```
  SUBTITLES_RULES=eng:fre;fre:off;default:fre,eng
  ```
* **A path to a standalone YAML file** (per concern), using a `rules:` list:
  ```yaml
  rules:
    - eng: [fre]
    - fre: [off]
    - default: [fre, eng]
  ```

### ⚠️ Precedence: env vars OVERRIDE the config file

> **If a `*_RULES` environment variable is set, it completely overrides that
> concern's section in the config file.** The override is **per concern**: e.g.
> setting `TITLE_RULES` replaces *only* the `title:` section; `audio`,
> `subtitles` and `poster` still come from the file. A concern you set via env
> var ignores its file section entirely (they are not merged — the env var
> wins outright).

### At least one rule set is required

Babelarr does nothing without rules, so this is almost always a misconfiguration.
**Startup fails** if no concern has rules from *either* source — set at least one
`*_RULES` variable or provide a config file with at least one concern.

---

## How each concern decides

### Audio — `AUDIO_RULES`

Keyed by the title's **original language**. Preferences are audio languages;
`original` resolves to the title's original language. For the chosen language,
Babelarr picks the track with the **most channels** that does not exceed
`MAX_AUDIO_CHANNELS` (if set), with codec quality breaking ties. Misses (no
matching rule, or no track in any preferred language) leave the current Plex
audio default as-is.

Common presets:

```
# "OV purist" — always original-language audio:
AUDIO_RULES=default:original

# Native-dub watcher — German where available, else the original:
AUDIO_RULES=default:deu,original

# Mixed — keep anime in Japanese, everything else in English:
AUDIO_RULES=jpn:jpn;default:eng
```

### Subtitles — `SUBTITLES_RULES`

Keyed by the **audio language that will actually play** — i.e. the track
`AUDIO_RULES` selected. If audio was left untouched, the language of Plex's
*current* default audio track is used (falling back to the original language
only when no default is marked). This means a native dub automatically gets the
subtitle rule for the dub's language, not the original's. First available
preference wins; `off` forces subtitles off. Misses leave subtitles untouched.

### Posters — `POSTER_RULES`

Keyed by the title's **original language**. Preferences are poster languages;
`original` resolves to the title's original language and `textless` matches
TMDB's no-language posters. Among candidates for a given preference, the
highest-voted poster on TMDB is chosen.

### Titles — `TITLE_RULES`

Keyed by the title's **original language**. Preferences are title languages;
`original` resolves to TMDB's original title. When Babelarr sets a title it also
**locks** the Plex title field so the agent won't revert it on the next refresh.

### Protecting hand-picked artwork/titles — `SKIP_USER_LOCKED` + state

When Babelarr sets a poster or title it **locks** the Plex field (so Plex's
agent won't revert it) and records a **fingerprint** of the value it wrote. On
later runs it compares that fingerprint to the field's current value to tell
*its own* locked value apart from one **you** locked by hand:

* locked field whose value still matches Babelarr's fingerprint → **ours** →
  Babelarr may update it (e.g. when your rules change);
* locked field whose value **doesn't** match (you edited/swapped it) or that
  Babelarr never set → **user-owned**.

`SKIP_USER_LOCKED` decides what happens to **user-owned** fields:

| Value | Effect |
|-------|--------|
| `true` / `all` | protect **both** user-locked posters and titles (default) |
| `false` / `none` | protect **neither** — overwrite even your hand-locked fields |
| `poster` | protect only user-locked **posters** |
| `title` | protect only user-locked **titles** |
| `poster,title` | both (same as `true`) |

(Babelarr's *own* locked fields are always re-manageable regardless — this guard
only concerns fields **you** locked.)

#### `STATE_PERSISTENCE` — how Babelarr remembers its own values (**required**)

Because this all hinges on fingerprints, you must choose where they're stored.
`STATE_PERSISTENCE` is **required** when poster or title rules are used (it has
**no default** — the choice is impactful). It's irrelevant, and not required,
for audio/subtitle-only setups.

| Value | Mechanism | Trade-off |
|-------|-----------|-----------|
| `file` | A JSON file (`STATE_FILE`, default `/config/babelarr-state.json`), keyed by each item's TMDB/IMDB guid. | Invisible to Plex; survives library rebuilds. External file to keep. If lost/corrupt, Babelarr forgets ownership and backs off (never clobbers). |
| `labels` | Plex labels `babelarr-locked:<field>:<hash>` on each item. | All state lives in Plex (nothing external). **But the labels are visible/editable in the Plex UI**: if you edit or remove one, that field looks user-owned and Babelarr stops managing it (safe, but it won't update on rule changes). |

Both mechanisms fail toward **"don't clobber."** `file` is the usual choice;
pick `labels` only if you specifically want all state inside Plex.

> Note on posters: Babelarr fingerprints the uploaded poster's resulting
> identifier, so if you later swap the poster by hand it's detected as yours and
> protected. If an item is removed/rebuilt or its uploaded images are purged by
> Plex, the poster field also loses its lock — so Babelarr simply sets it again
> (it never overwrites a *locked* poster it doesn't recognize).

---

## Configuration

Operational settings are environment variables (below). The four **rule sets**
come from a config file and/or `*_RULES` env vars — see
[Where rules come from](#where-rules-come-from-config-file-vs-env-vars).

| Variable | Default | Description |
|----------|---------|-------------|
| `PLEX_URL` | — (required) | Plex base URL, e.g. `http://192.168.1.10:32400`. Use the host's LAN IP, **not** `localhost`. |
| `PLEX_TOKEN` | — (required) | Plex authentication token. |
| `TMDB_API_KEY` | — (required) | TMDB API key (original language, posters, titles). |
| `PLEX_LIBRARIES` | *(all movie + show libraries)* | Comma-separated library names to process. |
| `CONFIG_FILE` | `/config/babelarr.yml` | Path to the unified config file (optional if you use env vars). |
| `AUDIO_RULES` | *(unset = untouched)* | Audio rule set. Token: `original`. **Overrides** the file's `audio:`. |
| `SUBTITLES_RULES` | *(unset = untouched)* | Subtitle rule set. Token: `off`. **Overrides** the file's `subtitles:`. |
| `POSTER_RULES` | *(unset = untouched)* | Poster rule set. Tokens: `original`, `textless`. **Overrides** the file's `poster:`. |
| `TITLE_RULES` | *(unset = untouched)* | Title rule set. Token: `original`. **Overrides** the file's `title:`. |
| `SKIP_USER_LOCKED` | `true` | Which **user-locked** fields to leave alone: `true`/`all`, `false`/`none`, `poster`, `title`, or `poster,title`. |
| `STATE_PERSISTENCE` | — (**required** if poster/title rules used) | How Babelarr remembers its own values: `file` or `labels`. No default. |
| `STATE_FILE` | `/config/babelarr-state.json` | Path to the JSON state file (only used when `STATE_PERSISTENCE=file`). |
| `MAX_AUDIO_CHANNELS` | *(unset = no cap)* | Ceiling on audio channels (e.g. `6` = 5.1). |
| `SWEEP_INTERVAL_MINUTES` | `360` | Cadence of the always-on full-library sweep. |
| `NEW_MEDIA_MODE` | `disabled` | `webhook`, `polling`, or `disabled`. |
| `WEBHOOK_PORT` | `9999` | Listener port when `NEW_MEDIA_MODE=webhook`. |
| `RECENT_POLL_INTERVAL_MINUTES` | `15` | Poll cadence when `NEW_MEDIA_MODE=polling`. |
| `DRY_RUN` | `false` | Log intended changes without applying them. |
| `TZ` | — | Container timezone. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, … |

---

## New-media handling (`NEW_MEDIA_MODE`)

The full-library sweep always runs (`SWEEP_INTERVAL_MINUTES`) as a safety net.
On top of that:

* **`webhook`** *(requires Plex Pass)* — Babelarr listens for Plex `library.new`
  events and processes just the new item immediately. Expose `WEBHOOK_PORT` and
  add a webhook in Plex (**Settings → Webhooks**) pointing at
  `http://<HOST-IP>:9999`.
* **`polling`** *(no Plex Pass)* — scans each library's recently-added items
  every `RECENT_POLL_INTERVAL_MINUTES`.
* **`disabled`** *(default)* — only the scheduled full sweep runs.

---

## Running

### docker-compose

See [`examples/docker-compose.example.yml`](examples/docker-compose.example.yml).
Minimal example (rules via env vars; alternatively mount a config file):

```yaml
services:
  babelarr:
    image: ghcr.io/ragepeanut/babelarr:latest
    container_name: babelarr
    environment:
      - PLEX_URL=http://192.168.1.10:32400
      - PLEX_TOKEN=xxxxxxxxxxxx
      - TMDB_API_KEY=xxxxxxxxxxxx
      - AUDIO_RULES=default:original
      - SUBTITLES_RULES=eng:fre;fre:off;default:fre,eng
      - POSTER_RULES=fre:fra;default:eng,textless
      - TITLE_RULES=jpn:original;cjk:eng;default:original
      - MAX_AUDIO_CHANNELS=6
      - NEW_MEDIA_MODE=webhook
    ports:
      - 9999:9999        # only when NEW_MEDIA_MODE=webhook
    restart: unless-stopped
```

### Build locally

```sh
docker build -t babelarr .
```

---

## Development

```sh
pip install -r requirements.txt pytest
pytest
```

The selection logic (`babelarr/selector.py`, `poster_selector.py`,
`title_selector.py`) and the rule engine (`babelarr/rules.py`,
`langscript.py`) are pure (no Plex/network dependencies) and fully unit-tested
in `tests/`.

---

## Notes & caveats

* **Default track selection is library-wide**, not per-Plex-user. Plex stores
  the default audio/subtitle stream on the media part, so these choices apply to
  everyone who plays the item.
* **TV**: a series' original language (from TMDB) is applied to all of its
  episodes; poster/title rules apply to the series item.
* Original language, posters, and titles come from TMDB via the item's
  `tmdb://` guid (falling back to `imdb://` / `tvdb://` lookups). Titles Plex
  hasn't matched to a provider are skipped.
* `textless` posters are only as accurate as TMDB's community categorization
  (see above).
