# Babelarr

**Make Plex speak your languages.**

Plex isn't built for polyglots — Babelarr fixes that. For every movie and show,
it looks up the title's **original language** from TMDB and, driven by three
independent sets of per-language rules, can:

1. Set the default **audio** track to the original language (best quality, with
   an optional channel-count ceiling) and the **subtitle** track according to
   your rules.
2. Set the **poster** to a chosen language — or a textless one.
3. Set the **display title** to a chosen language — or the original.

Each concern is **opt-in**: leave its rule set unset and Babelarr won't touch
it. It runs as a single Docker container that fits a typical \*arr stack. A full
library sweep always runs on a schedule; optionally, new media is handled
near-instantly via a Plex **webhook** (Plex Pass) or by **polling** recently
added items (no Plex Pass).

> Plex runs on the host (e.g. your NAS) — Babelarr talks to it over the network
> via `PLEX_URL`. Plex does **not** need to be in the same compose stack.

> **Renamed from Lingarr.** Babelarr is the evolution of Lingarr (which only did
> audio/subtitle tracks). The track behavior is unchanged in spirit; posters and
> titles are new, and the rule grammar was unified (see
> [Migration](#migration-from-lingarr)).

---

## The rule model (read this first)

All three concerns — subtitles, posters, titles — share **one** rule grammar.

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

| Token | Subtitles | Posters | Titles |
|-------|-----------|---------|--------|
| `original` | the original-language subtitle track | — | TMDB **original title** |
| `off` | force subtitles **OFF** (distinct from "untouched") | — | — |
| `textless` | — | TMDB **"no language"** poster¹ | — |

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

### Inline vs. YAML

Every `*_RULES` variable accepts either form (auto-detected):

* **Inline string** — entries separated by `;`, preferences by `,`:
  ```
  SUBTITLE_RULES=eng:fre;fre:off;default:fre,eng
  ```
* **YAML file** — mount it and point the variable at the path. Use a **list** so
  order is explicit:
  ```yaml
  rules:
    - eng: [fre]
    - fre: [off]
    - default: [fre, eng]
  ```
  See the examples in [`config/`](config/).

Language codes may be written in any ISO 639 form — `fr`, `fre`, and `fra` are
all understood and compared equivalently.

---

## How each concern decides

### Audio

Among the audio tracks whose language matches the title's TMDB
`original_language`, Babelarr picks the one with the **most channels** that does
not exceed `MAX_AUDIO_CHANNELS` (if set); codec quality breaks ties. If no track
matches the original language, the current Plex audio default is left as-is.
(Audio has no rule set of its own — it always targets the original language.)

### Subtitles — `SUBTITLE_RULES`

Keyed by the **audio language that will actually play** (i.e. the track audio
selected above, falling back to the original language). First available
preference wins; `off` forces subtitles off; `original` resolves to the title's
original language. Misses leave subtitles untouched.

### Posters — `POSTER_RULES`

Keyed by the title's **original language**. Preferences are poster languages;
`textless` matches TMDB's no-language posters. Among candidates for a given
preference, the highest-voted poster on TMDB is chosen.

### Titles — `TITLE_RULES`

Keyed by the title's **original language**. Preferences are title languages;
`original` resolves to TMDB's original title. When Babelarr sets a title it also
**locks** the Plex title field so the agent won't revert it on the next refresh.

### Protecting hand-picked artwork/titles — `ONLY_REPLACE_UNLOCKED`

Defaults to `true`: Babelarr will **not** overwrite a poster or title whose Plex
field you've **locked** (hand-picked). Set it to `false` to let Babelarr manage
even locked fields.

---

## Configuration

All configuration is via environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `PLEX_URL` | — (required) | Plex base URL, e.g. `http://192.168.1.10:32400`. Use the host's LAN IP, **not** `localhost`. |
| `PLEX_TOKEN` | — (required) | Plex authentication token. |
| `TMDB_API_KEY` | — (required) | TMDB API key (original language, posters, titles). |
| `PLEX_LIBRARIES` | *(all movie + show libraries)* | Comma-separated library names to process. |
| `SUBTITLE_RULES` | *(unset = untouched)* | Subtitle rule set. Tokens: `off`, `original`. |
| `POSTER_RULES` | *(unset = untouched)* | Poster rule set. Token: `textless`. |
| `TITLE_RULES` | *(unset = untouched)* | Title rule set. Token: `original`. |
| `ONLY_REPLACE_UNLOCKED` | `true` | Skip posters/titles whose Plex field is locked (hand-picked). |
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

See [`docker-compose.yml`](docker-compose.yml). Minimal example:

```yaml
services:
  babelarr:
    image: ghcr.io/ragepeanut/babelarr:latest
    container_name: babelarr
    environment:
      - PLEX_URL=http://192.168.1.10:32400
      - PLEX_TOKEN=xxxxxxxxxxxx
      - TMDB_API_KEY=xxxxxxxxxxxx
      - SUBTITLE_RULES=eng:fre;fre:off;default:fre,eng
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

## Migration from Lingarr

* The package/image is now **`babelarr`** (was `lingarr`).
* **Breaking: subtitle "OFF".** Previously an *empty* preference list meant
  "subtitles off". Now use the explicit **`off`** token (`fre:off`). An empty
  list / a matched-but-unavailable rule now means **leave untouched** (let Plex
  decide) — a deliberately different outcome from `off`.
* **New:** `POSTER_RULES`, `TITLE_RULES`, `ONLY_REPLACE_UNLOCKED`, script-class
  keys, and the `original` / `textless` tokens.
* Rule sets are now **ordered and validated**; see
  [the rule model](#the-rule-model-read-this-first).

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
