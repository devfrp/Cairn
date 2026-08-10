# Cairn

*[Version française](README.md)*

Health and training tracking, on your own machine. Cairn brings your Garmin
Connect and Strava data together in a file that belongs to you, and sets
recovery against training load — something neither service does, since they
don't talk to each other.

Nothing leaves your machine beyond the calls needed to fetch your own data.

## What it does

- **Daily sync** from both sources, keeping the raw responses and your
  original `.fit` files.
- **No more duplicates**: a ride recorded on your watch lands in both Garmin
  and Strava. Cairn keeps one, with the sensors from the first and the gear
  from the second.
- **Recovery and load**: a night's sleep, heart rate variability and resting
  rate set against the same day's rides.
- **Chronic and acute load**, compared against your own averages, with
  overload detection.
- **Built-in analysis**: time per heart rate zone, volume per discipline,
  load week after week.
- **Weekly summary** written by a model running on your own machine, and
  optionally sent to Discord.

Project site: <https://devfrp.github.io/Cairn/en/>

## Install the app

Cairn is a **self-contained desktop application**: it carries its database
and its model. Shipped as an AppImage on Linux, a zip on Windows and a `.dmg`
on macOS, with nothing else to install:

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

Both accounts are linked from the application's **Settings** page.

### Garmin

Since March 2026, Garmin turns away connections that don't come from a
browser: it inspects the signature of the encrypted layer and rejects software
clients before even looking at credentials. That is also what ended `garth`,
the library Cairn builds on.

So Cairn opens **Garmin's own sign-in page in a window**. Your password, and
your two-factor code if you use one, go straight to Garmin; they never pass
through Cairn's code, which comes away with nothing but a token — encrypted in
your data file and valid for about a year.

That is why a web rendering engine ships with the application, and a good part
of why the packages are large. It serves that one page.

`cairn auth garmin` still exists for accounts on a domain that doesn't filter,
but fails on an ordinary account. The command-line service shares the
application's data file, so the token obtained in the window works for it too.

### Strava

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

The service runs at `CAIRN_SYNC_HOUR`. Until the full history has been
pulled in, it follows each daily sync with a backfill batch.

```bash
# cursor state and recent runs
cairn status

# manual pass
cairn sync --source garmin --mode incremental

# on-demand history backfill
cairn sync --mode backfill
```

## Weekly summary

**The application carries its own model.** Nothing to install: Ollama and
`qwen3:0.6b` travel with it and start on demand on a local port. The summary
works offline and the text never leaves your machine.

That, together with the rendering engine behind the Garmin sign-in window, is
what makes each build weigh a little over a gigabyte — two bulky components
for two features that would otherwise mean installing something else
alongside.

If an Ollama address is set in the settings, that one is used instead — handy
for a larger model already running elsewhere.

Cairn computes the loads, the averages, the deviations from your habits and
the fatigue signals itself. The model only **puts those values into words**:
it is never asked to count. Its reply is then checked, and any figure absent
from the briefing has it discarded in favour of a pre-written text — less
fluent, but never wrong.

That is what makes such a small model worth bundling: 0.6 billion parameters
cannot reason, but they write up established figures perfectly well.

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
  sync/            data translation, duplicate merging, orchestration
  metrics/         chronic and acute load, baselines, signals
  insights/        weekly summary, bundled engine, Discord delivery
packaging/         PyInstaller recipes, icons, systemd unit
```

The database is a SQLite file in three layers, marked by table prefix:
`raw_` (untouched payloads, the source of truth), unprefixed (normalised
model) and `marts_` (aggregates). Raw data is never discarded: if a mapper
needs fixing, the transformation replays without hitting the APIs again.
