# Babelarr

**Make Plex speak your languages.**

Plex isn't built for polyglots — Babelarr fixes that. For every movie and show,
it works out the title's language from TMDB and, driven by four independent sets
of per-language rules, can:

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

* **key** — matched against the title's language. For audio/subtitles that's
  the **content language**; for posters/titles it's the **production language**
  (see [Two languages](#two-languages-content-vs-production)). It may be:
  * a concrete language code (`fre`, `ja`, `de`, …), any ISO 639 form;
  * a **script class** (see below) — `cjk`, `kana`, `cyrillic`, …;
  * `xx` / `silent` — matches [silent / no-language](#silent--no-language-titles)
    content (useful mainly in `SUBTITLES_RULES`);
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
| `original` | the **content**-language audio track | — | the **production**-language poster | the **production**-language (TMDB original) title |
| `off` | — | force subtitles **OFF** (distinct from "untouched") | — | — |
| `textless` | — | — | TMDB **"no language"** poster¹ | — |

> **`original` means two different things** depending on the concern, because
> Babelarr distinguishes a title's **content language** (what it's actually
> spoken in) from its **production language** (TMDB `original_language`). Audio
> `original` → the content language; poster/title `original` → the production
> language. See [Two languages: content vs. production](#two-languages-content-vs-production).

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

Keys can match the **writing system** of the title's language (content or
production, depending on the concern), so you can make coarse decisions without
listing every language. `cjk` is an umbrella over three leaves:

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

## Two languages: content vs. production

TMDB exposes **two** notions of a title's language, and they are **not always
the same**:

* **Production language** — TMDB's `original_language`. It reflects the
  **origin/production** of the title (origin country, producing studio), **not
  necessarily what it's spoken in**. For example the anime *Uzumaki* has
  `original_language = en` because it was an Adult Swim production — even though
  it's spoken in Japanese.
* **Content language** — what the title is actually **spoken in**, derived from
  TMDB's `spoken_languages`.

Babelarr deliberately uses the **right one for each concern**:

| Concern | Language used | Why |
|---------|---------------|-----|
| **audio**, **subtitles** | **content** language | you want the track in the language the title is actually spoken in |
| **poster**, **title** | **production** language | "the original poster/title" conventionally means the *production* one (e.g. *Uzumaki*'s official English title/poster) |

So for *Uzumaki* with `AUDIO_RULES=default:original`, `SUBTITLES_RULES=...`,
`POSTER_RULES=default:original`, `TITLE_RULES=default:original` you get
**Japanese audio + subtitles** (content) but the **English title and poster**
(production) — which is almost certainly what you want.

### How the content language is resolved

For audio/subtitles, Babelarr determines the content language from TMDB
**metadata only** (no inspection of the file's audio tracks), in this order:

1. **Manual override** — a `babelarr-ov:<lang>` **label** on the item (see
   below) wins outright, including `babelarr-ov:silent`.
2. **Only "no language" spoken** (a silent / music-only title, where TMDB's
   `spoken_languages` is just `xx`) → the special **no-language** result (see
   [Silent / no-language titles](#silent--no-language-titles)).
3. **Exactly one** real spoken language → use it.
4. **Multiple** spoken, and the **production** language is among them → use the
   **production** language. (The order of `spoken_languages` is *not* a reliable
   priority signal — e.g. *Nine to Five* lists `[French, English]` though it's an
   English film — so the authoritative `original_language` wins when present.)
5. **Multiple** spoken, production **not** among them → the **first** spoken.
6. **No** spoken languages at all → fall back to the production language.

### Manual override — the `babelarr-ov:<lang>` label

When TMDB's data is odd (locked, mis-tagged, an unusual co-production), add a
Plex **label** `babelarr-ov:<lang>` to the movie or show to force its **content
language** (audio/subtitles only — posters/titles still use the production
language). `<lang>` accepts any ISO 639 form (`ja` / `jpn`, `fr` / `fre` /
`fra`), plus `xx` / `silent` to force no-language. Applied to a show, it is
inherited by all episodes.

```
# Force Uzumaki's content language to Japanese regardless of TMDB:
babelarr-ov:ja
```

> This label is **not** one of the `babelarr-locked:*` state labels; it's a
> manual input you add yourself.

### Silent / no-language titles

Some titles are silent or music-only; TMDB marks these with a single `xx` ("No
Language") in `spoken_languages` (e.g. *Metropolis*, 1927). Babelarr resolves
their content language to a dedicated **no-language** value, which means:

* **Audio** → left **untouched** (there's no spoken language to select; if the
  file somehow has an explicit no-language audio track, that is chosen).
* **Subtitles** → still rule-driven, treating "no language" as the content
  language. Target it with an **`xx`** (or the friendlier alias **`silent`**)
  key, e.g. `SUBTITLES_RULES=silent:off;...`. With no `xx`/`silent` rule it
  falls through to `default` like any other language.

```
# Silent films: no subtitles; English audio -> French subs; else untouched
SUBTITLES_RULES=silent:off;eng:fre
```

> Note: `xx` is TMDB's code for **no language** in `spoken_languages`. For
> *poster* images TMDB uses `null` instead — Babelarr already handles that via
> the poster `textless` token; the two are unrelated conventions.

---

## How each concern decides

### Audio — `AUDIO_RULES`

Keyed by the title's **content language** (what it's spoken in — see above).
Preferences are audio languages; `original` resolves to that content language.
For the chosen language, Babelarr picks the track with the **most channels**
that does not exceed `MAX_AUDIO_CHANNELS` (if set), with codec quality breaking
ties. Misses (no matching rule, or no track in any preferred language) leave the
current Plex audio default as-is.

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
*current* default audio track is used (falling back to the content language only
when no default is marked). This means a native dub automatically gets the
subtitle rule for the dub's language. First available preference wins; `off`
forces subtitles off. Misses leave subtitles untouched.

### Posters — `POSTER_RULES`

Keyed by the title's **production language** (TMDB `original_language`).
Preferences are poster languages; `original` resolves to that production
language and `textless` matches TMDB's no-language posters. Among candidates for
a given preference, the highest-voted poster on TMDB is chosen. If that poster
is already one of the item's poster candidates in Plex, Babelarr **selects** it;
otherwise it **uploads** it.

### Titles — `TITLE_RULES`

Keyed by the title's **production language** (TMDB `original_language`).
Preferences are title languages; `original` resolves to TMDB's original title
(the production-language title). When Babelarr sets a title it also **locks** the
Plex title field so the agent won't revert it on the next refresh.

### Protecting hand-picked artwork/titles — `SKIP_USER_LOCKED` + state

When Babelarr sets a poster or title it **locks** the Plex field (so Plex's
agent won't revert it) and records a **fingerprint** of what it set. On later
runs it compares to tell *its own* value apart from one **you** set by hand:

* **Titles** fingerprint the title *string*. So Babelarr recognizes its own
  title (and won't rewrite it needlessly), and if you change `TITLE_RULES` the
  new title *does* get applied.
* **Posters** record the *selected poster's key* (what's actually in effect —
  the TMDB URL when selected from candidates, else the upload id). For an
  *uploaded* poster (whose key isn't the URL) Babelarr also records the source
  URL, so a later rule change is detectable; selected posters need no second
  value. This lets Babelarr recognize the poster in effect — so it won't
  re-upload/re-select every sweep (uploaded posters included), re-applies when a
  rule now wants a different poster, and leaves a poster you swapped in by hand
  alone.

A locked field whose fingerprint doesn't match (you edited/swapped it, or
Babelarr never set it) is treated as **user-owned**.

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

> **Posters — selecting vs. uploading.** If the wanted poster is already one of
> the item's existing poster candidates on TMDB (matched by URL), Babelarr
> **selects** it rather than re-uploading — no duplicate image, and the chosen
> poster stays a clean provider entry. Only when the URL isn't among the
> candidates does it upload.
>
> Babelarr records the key of the poster actually in effect, so it recognizes
> its own poster (no re-upload every sweep), re-applies when you change
> `POSTER_RULES`, and leaves a poster you swapped in by hand alone (when
> `poster` is protected by `SKIP_USER_LOCKED`).

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
| `PLEX_TIMEOUT` | `120` | Per-request Plex read timeout (seconds). Raise it if large libraries time out. |
| `PLEX_RETRIES` | `3` | Attempts for transient Plex failures (library/episode enumeration) before skipping, with exponential backoff. |
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

## Resilience

Plex can be slow to respond on large libraries or a busy NAS. Babelarr is built
so a single slow/failed Plex call never sinks a whole run:

* Per-request timeout is `PLEX_TIMEOUT` seconds (default `120`).
* The big enumeration calls (listing a library's items, a show's episodes) are
  retried up to `PLEX_RETRIES` times (default `3`) with exponential backoff.
* If a section still fails after retries, it is **skipped** and the sweep moves
  on to the others; a single bad **item** is logged and skipped too.
* Each sweep ends with a summary line, e.g.
  `Full sweep complete (processed=1234, skipped=2)`, so problems are visible.

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
* **TV**: a series' production language (from TMDB) drives its poster/title. The
  content language for audio/subtitles is resolved **per episode** (using each
  episode file's own audio tracks), inheriting the series' TMDB metadata.
* Language metadata, posters, and titles come from TMDB via the item's
  `tmdb://` guid (falling back to `imdb://` / `tvdb://` lookups). Titles Plex
  hasn't matched to a provider are skipped (unless a `babelarr-ov:` label
  supplies the content language).
* `textless` posters are only as accurate as TMDB's community categorization
  (see above).
