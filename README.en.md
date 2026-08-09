# Cairn

*[Version française](README.md)*

Self-hosted health and training tracker. Cairn pulls your Garmin Connect and
Strava data into a single file you fully control, and cross-references
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
- **Built-in analysis**: time per heart rate zone, volume per discipline,
  load week after week.
- **Weekly summary** written by an Ollama model running on your own hardware,
  optionally delivered to Discord.

Project site: <https://devfrp.github.io/Cairn/en/>

## Install the app

Cairn is a **self-contained desktop application**: it carries its database,
its language engine and its model. Shipped as an AppImage on Linux, a zip on
Windows and a `.dmg` on macOS, with nothing else to install:

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

- A Garmin Connect account.
- A Strava application — create one at <https://www.strava.com/settings/api>.

## First launch

Nothing to prepare. On opening, the **Settings** page offers a location for
the data file, generates the key that encrypts your tokens, installs the
schema and links your accounts.

The `cairn.db` file holds everything. Backing it up backs up Cairn; copying it
to another machine moves Cairn there.

## Automatic syncing

The application syncs on demand. For a hands-off nightly pass on a machine
that stays on:

```bash
sudo install -d -o cairn -g cairn /opt/cairn /var/lib/cairn
sudo -u cairn python3 -m venv /opt/cairn/venv
sudo -u cairn /opt/cairn/venv/bin/pip install 'cairn @ git+https://github.com/devfrp/Cairn'
sudo install -D -m 600 .env.example /etc/cairn/cairn.env
sudo cp packaging/systemd/cairn.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now cairn
```

The service shares the application's data file: point `CAIRN_DB_PATH` at the
same path.

## Authentication

The desktop application links both accounts from its **Settings** page. On
the command line, for the service:

Garmin has no public API for individual use: Cairn opens an SSO session and
keeps the OAuth token (valid for roughly a year), encrypted at rest. Your
password is not stored.

```bash
cairn auth garmin
```

If two-factor authentication is enabled, the code is prompted for here.

Strava uses a two-step OAuth2 flow:

```bash
cairn auth strava
```

The command prints a URL. Open it, authorise the application, then take the
`code` parameter from the redirect URL and run:

```bash
cairn auth strava --code PASTE_CODE_HERE
```

## Usage

The service runs at `CAIRN_SYNC_HOUR`. Until the full
history has been pulled in, it follows the daily sync with a backfill batch.

```bash
# cursor state and recent runs
cairn status

# manual pass
cairn sync --source garmin --mode incremental

# on-demand history backfill
cairn sync --mode backfill
```

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
cairn check-ollama
cairn summary
cairn summary --send
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
packaging/         PyInstaller recipes, icons, systemd unit
```

The database is a SQLite file in three layers, marked by table prefix:
`raw_` (untouched payloads, the source of truth), unprefixed (normalised
model) and `marts_` (aggregates). Raw data is never discarded: if a mapper
needs fixing, the transformation replays without hitting the APIs again.
