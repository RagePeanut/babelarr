# Lingarr

Automatic Plex **audio and subtitle track selection**, driven by each title's
**original language**.

For every movie and episode, Lingarr:

1. Looks up the title's **original language** from TMDB.
2. Sets the **audio** track to that original language (picking the best quality
   available, with an optional channel-count ceiling).
3. Enables (or disables) **subtitles** according to configurable per-audio-language
   rules.

It runs as a single Docker container that fits a typical \*arr stack. A full
library sweep always runs on a schedule; optionally, new media is handled
near-instantly via a Plex **webhook** (Plex Pass) or via **polling** of
recently-added items (no Plex Pass).

> Plex runs on the host (e.g. your NAS) — Lingarr talks to it over the network
> via `PLEX_URL`. Plex does **not** need to be in the same compose stack.

---

## How it decides

### Audio

Among the audio tracks whose language matches the title's TMDB
`original_language`, Lingarr picks the one with the **most channels** that does
not exceed `MAX_AUDIO_CHANNELS` (if set). Codec quality breaks ties. If no track
matches the original language, the current Plex audio default is left as-is.

### Subtitles

Subtitle behaviour is defined by `SUBTITLE_RULES` — a mapping from the **audio
language that will play** to an ordered list of **subtitle language
preferences** (first available wins):

```
eng:fre          English audio  -> French subs, else OFF
fre:             French audio   -> subtitles OFF
default:fre,eng  anything else  -> French, then English, else OFF
```

Key semantics:

* The first available preference in a rule wins.
* An **empty** preference list means "subtitles OFF for this audio language".
* **Once a specific rule matches, it is authoritative.** If none of its
  preferences exist, subtitles are turned **OFF** — it does **not** fall through
  to `default`. This is why English audio with no French subtitles results in no
  subtitles at all (rather than falling back to English subs).
* `default` applies to any audio language without an explicit rule.
* If `SUBTITLE_RULES` is **unset**, subtitles are left completely untouched
  (audio is still set).

Language codes may be written in any ISO 639 form — `fr`, `fre`, and `fra` are
all understood and compared equivalently.

---

## Configuration

All configuration is via environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `PLEX_URL` | — (required) | Plex base URL, e.g. `http://192.168.1.10:32400`. Use the host's LAN IP, **not** `localhost`. |
| `PLEX_TOKEN` | — (required) | Plex authentication token. |
| `TMDB_API_KEY` | — (required) | TMDB API key for original-language lookup. |
| `PLEX_LIBRARIES` | *(all movie + show libraries)* | Comma-separated library names to process. |
| `SUBTITLE_RULES` | *(unset = subs untouched)* | Inline rule string **or** path to a YAML file (auto-detected). |
| `MAX_AUDIO_CHANNELS` | *(unset = no cap)* | Ceiling on audio channels (e.g. `6` = 5.1). Best matching track ≤ this is chosen. |
| `SWEEP_INTERVAL_MINUTES` | `360` | Cadence of the always-on full-library sweep. |
| `NEW_MEDIA_MODE` | `disabled` | `webhook`, `polling`, or `disabled`. |
| `WEBHOOK_PORT` | `9999` | Listener port when `NEW_MEDIA_MODE=webhook`. |
| `RECENT_POLL_INTERVAL_MINUTES` | `15` | Poll cadence when `NEW_MEDIA_MODE=polling`. |
| `DRY_RUN` | `false` | Log intended changes without applying them. |
| `TZ` | — | Container timezone. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, … |

### `SUBTITLE_RULES`: inline vs. file

The same variable accepts either form; Lingarr detects which:

* **Inline string** — no volume needed:
  ```
  SUBTITLE_RULES=eng:fre;fre:;default:fre,eng
  ```
* **YAML file** — mount it and point the variable at the path:
  ```
  SUBTITLE_RULES=/config/subtitle_rules.yml
  ```
  See [`config/subtitle_rules.example.yml`](config/subtitle_rules.example.yml).

---

## New-media handling (`NEW_MEDIA_MODE`)

The full-library sweep always runs (`SWEEP_INTERVAL_MINUTES`) as a safety net.
On top of that:

* **`webhook`** *(requires Plex Pass)* — Lingarr listens for Plex `library.new`
  events and processes just the new item immediately. Expose `WEBHOOK_PORT` and
  add a webhook in Plex (**Settings → Webhooks**) pointing at
  `http://<HOST-IP>:9999`.
* **`polling`** *(no Plex Pass)* — Lingarr scans each library's recently-added
  items every `RECENT_POLL_INTERVAL_MINUTES`.
* **`disabled`** *(default)* — only the scheduled full sweep runs.

---

## Running

### docker-compose

See [`docker-compose.yml`](docker-compose.yml). Minimal example:

```yaml
services:
  lingarr:
    image: ghcr.io/ragepeanut/lingarr:latest
    container_name: lingarr
    environment:
      - PLEX_URL=http://192.168.1.10:32400
      - PLEX_TOKEN=xxxxxxxxxxxx
      - TMDB_API_KEY=xxxxxxxxxxxx
      - SUBTITLE_RULES=eng:fre;fre:;default:fre,eng
      - MAX_AUDIO_CHANNELS=6
      - SWEEP_INTERVAL_MINUTES=360
      - NEW_MEDIA_MODE=webhook
    ports:
      - 9999:9999        # only when NEW_MEDIA_MODE=webhook
    restart: unless-stopped
```

### Build locally

```sh
docker build -t lingarr .
```

---

## Development

```sh
pip install -r requirements.txt pytest
pytest
```

The selection logic in `lingarr/selector.py` is pure (no Plex/network
dependencies) and fully unit-tested in `tests/`.

## Notes & caveats

* **Default track selection is library-wide**, not per-Plex-user. Plex stores
  the default audio/subtitle stream on the media part, so these choices apply to
  everyone who plays the item. True per-user preferences are a separate Plex
  feature and are out of scope here.
* **TV**: a series' original language (from TMDB) is applied to all of its
  episodes.
* Original language comes from TMDB via the item's `tmdb://` guid (falling back
  to `imdb://` / `tvdb://` lookups). Titles Plex hasn't matched to a provider
  are skipped.
