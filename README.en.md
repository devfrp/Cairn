# Cairn

*[Version française](README.md)*

Self-hosted health and training tracker. Cairn pulls your Garmin Connect and
Strava data into a PostgreSQL database you fully control, and cross-references
recovery against training load — something neither service does, since they
don't talk to each other.

No data leaves your server beyond the API calls needed to synchronise.

## What it does

- **Daily sync** from both sources, archiving the raw JSON and the original
  `.fit` files.
- **Deduplication**: a ride recorded on your watch shows up in Garmin *and* in
  Strava. Cairn recognises them as one activity and merges the metrics —
  sensors from Garmin, gear and segments from Strava.
- **Recovery × load**: a night's sleep, HRV and resting heart rate placed
  alongside the same day's rides.
- **Grafana dashboards**, version-controlled in the repo.
- **Weekly summary** written by an Ollama model running on your own hardware,
  optionally delivered to Discord.

Project site: <https://devfrp.github.io/Cairn/en/>

## Install the app

Cairn has two parts: a **server** (PostgreSQL with TimescaleDB, Grafana)
deployed by docker-compose, and a **desktop application** that connects to it.
The application ships as a self-contained package — AppImage on Linux, a zip
on Windows, `.dmg` on macOS — with no Python interpreter and no language
engine to install:

```bash
curl -fsSL https://devfrp.github.io/Cairn/get.sh | sh
```

Files are attached to every
[release](https://github.com/devfrp/Cairn/releases/latest) along with their
SHA-256 checksums. They are neither signed nor notarised, so Windows and macOS
will warn you on first launch.

The application is **portable**: put a `cairn.ini` file next to the executable
(the "portable mode" button in settings does it for you) and it becomes fully
self-contained, runnable from a USB stick.

The same release ships the command line (`cairn-cli-*`) for scheduled jobs;
it is also bundled inside the AppImage and the dmg.

## Requirements

- A Docker host (a Debian LXC on Proxmox works well).
- A Garmin Connect account.
- A Strava application — create one at <https://www.strava.com/settings/api>.

## Install

```bash
git clone https://github.com/devfrp/Cairn.git && cd Cairn
cp .env.example .env
```

Generate the credential encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put that value in `CAIRN_SECRET_KEY`, then fill in `POSTGRES_PASSWORD`,
`GRAFANA_PASSWORD` and your Strava credentials. Start it:

```bash
docker compose up -d
```

Migrations run on first start.

## Authentication

Garmin has no public API for individual use: Cairn opens an SSO session and
keeps the OAuth token (valid for roughly a year), encrypted at rest. Your
password is not stored.

```bash
docker compose run --rm cairn auth garmin
```

If two-factor authentication is enabled, the code is prompted for here.

Strava uses a two-step OAuth2 flow:

```bash
docker compose run --rm cairn auth strava
```

The command prints a URL. Open it, authorise the application, then take the
`code` parameter from the redirect URL and run:

```bash
docker compose run --rm cairn auth strava --code PASTE_CODE_HERE
```

## Usage

The nightly pass runs automatically at `CAIRN_SYNC_HOUR`. Until the full
history has been pulled in, it follows the daily sync with a backfill batch.

```bash
# cursor state and recent runs
docker compose run --rm cairn status

# manual pass
docker compose run --rm cairn sync --source garmin --mode incremental

# on-demand history backfill
docker compose run --rm cairn sync --mode backfill
```

Grafana listens on port 3000, with the data source and dashboards already
provisioned.

## Weekly summary

**The application ships with its own language engine.** Nothing to install:
the package carries Ollama and the `qwen3:0.6b` model, started on demand on a
local port. The summary works offline and the text never leaves your machine.
That is what makes the packages large, around 800 MB.

If an Ollama address is set in the settings, it is used instead of the
bundled engine — handy for a larger model already running on your server.

Cairn computes loads, averages, baseline deviations and fatigue signals
itself. The model only **writes those values up** — it is never asked to
count. The reply is then checked: any number absent from the briefing gets
the text rejected in favour of a deterministic write-up, less fluent but
never wrong.

That is what makes such a small model worth bundling: 0.6 billion parameters
cannot reason, but they turn established figures into fluent French just
fine.

```bash
docker compose run --rm cairn check-ollama
docker compose run --rm cairn summary
docker compose run --rm cairn summary --send
```

`--no-model` forces the deterministic write-up, handy for comparison. The
scheduler produces and sends the summary every week, on the day set by
`CAIRN_SUMMARY_WEEKDAY`.

## Backfill pacing

The two sources have different constraints:

| Source | Constraint | Consequence |
|---|---|---|
| Garmin | One request per day per metric | ~7 requests per day of history. `CAIRN_BACKFILL_DAYS_PER_RUN` caps the batch size per pass. |
| Strava | ~100 requests / 15 min, ~1000 / day | Activities come in pages of 200. When the daily quota runs out, the pass stops and resumes the next night. |

Either way the position is recorded in `sync_cursor`: an interruption never
restarts from scratch.

## Architecture

```
db/migrations/     SQL schema, applied by `cairn db upgrade`
src/cairn/
  gui/             desktop application (Qt): pages, settings, background tasks
  sources/         Garmin (garth) and Strava (OAuth2 + quota) clients
  sync/            mappers, deduplication, upserts, orchestration
packaging/         PyInstaller recipes, icons
grafana/           provisioned data source and dashboards
```

The database has three layers: `raw` (untouched payloads, the source of
truth), `public` (normalised model) and `marts` (aggregates for Grafana). Raw
data is never discarded: if a mapper needs fixing, the transformation replays
without hitting the APIs again.

Time series (activity streams, intraday measurements) are compressed
TimescaleDB hypertables.
