# dmarcParser

Interactive CLI + REPL to ingest and analyze **DMARC aggregate (RUA)** reports at scale.

## What it does

- **Ingests** DMARC XML from files on disk (`.xml`, `.gz`, `.zip`) and de‑duplicates by XML hash
- Stores data in a **per‑client SQLite DB** for incremental runs
- Provides **threat‑hunting views** (summary, domains, source IPs, fail rates, alignment indicators)
- Ships with an **interactive REPL** (history per client) and **direct CLI subcommands**

> **Current intended workflow**
>
> You have a folder on a Linux filesystem that already contains **extracted DMARC XML files** (optionally with some `.gz`/`.zip` artifacts mixed in). Point `dP` at that folder to ingest and analyze. (Roadmap: ingesting individual archives from the command line is planned, but not finalized yet.)

---

## Requirements

- Python **3.9+** (`python3 --version`)
- Linux/macOS (Windows works in WSL)
- Recommended: `pipx` or a virtual environment

---

## Installation

### Option A: Editable install (dev workflow)

````bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
````

### Option B: User install with `pipx`

````bash
pipx install .
````

This will expose the `dP` command on your `$PATH`.

---

## Usage

### 1) Prepare your input directory

Place your DMARC aggregate reports in a directory, e.g.:

````text
/var/dmarc/acme/      # contains *.xml (and possibly *.gz / *.zip)
/var/dmarc/acme/2024/  google.com!example.com!1719446400!1719532799.xml
/var/dmarc/acme/2024/  yahoo.com!example.com!1717113600!1717199999.xml
````

> If you have compressed collections, you can still point at the **root**; `dP` will discover `.xml`, `.gz`, and `.zip` and only ingest valid XML content inside.

### 2) Ingest into a per‑client database

````bash
# create or reuse a client DB called "AcmeCo" and ingest everything under /var/dmarc/acme
dP ingest /var/dmarc/acme --client AcmeCo

# Re-scan the same tree to pick up new files since last run
dP ingest /var/dmarc/acme --client AcmeCo --rescan
````

Databases are stored under:

````text
~/.dmarcParser/clients/<CLIENT>.db
````

### 3) Explore the data (CLI)

````bash
# High-level summary (optionally limited to last N days)
dP summary --client AcmeCo --days 30

# Top domains by failures / fail rate
dP domains --client AcmeCo --limit 25 --sort fail_rate --fail-only

# Top failing source IPs (optionally limited to last N days)
dP ips --client AcmeCo --limit 50 --days 14
````

### 4) Interactive REPL

````bash
# Launch shell and select or create a client
dP
````

Inside the shell:

````text
help                # list commands
clients             # show known client DBs
client <name>       # switch/create client and set context
ingest <path>       # ingest (uses current client)
rescan [path]       # re-run ingest decisions on path/root
summary [--days N]  # quick overview
domains [...]       # aggregate by header_from
ips [...]           # aggregate by source IP
clear               # clear the screen
exit / quit         # close shell
````

The REPL keeps arrow‑key history per client (~50 entries) under:

````text
~/.dmarcParser/history/<CLIENT>.history
````

---

## Examples

````bash
# First run
dP ingest /srv/dmarc/acme --client AcmeCo

# 30‑day pulse
dP summary --client AcmeCo --days 30

# Investigate failing senders
dP ips --client AcmeCo --days 7 --limit 100
````

---

## Data model (high level)

- **reports**: One row per DMARC aggregate report (metadata + policy)
- **records**: Flattened rows per `<record>` with counts, auth results, source IP, `header_from`, etc.
- **ingest_sessions / ingest_decisions**: Provenance and dedup decisions

> Tables and indices are created automatically on first ingest.

---

## Where to put sample files

The repo includes sample DMARC XMLs under `samples_user/`. You can point the tool at that directory to see example output:

````bash
dP ingest ./samples_user --client SampleCo --rescan
dP summary --client SampleCo
````

---

## Troubleshooting

- **Command not found (`dP`)** → If you used a virtualenv, activate it first (`source .venv/bin/activate`). If using `pipx`, ensure `~/.local/bin` is on `PATH`.
- **No new files ingested** → Use `--rescan` if you’ve added files in place since the last run.
- **SQLite busy/locked** → Close other sessions using the same client DB and retry.
- **XML ignored** → Only valid DMARC aggregate XML is ingested. Bad or non‑DMARC XML will be skipped.

---

## Roadmap

- Direct ingest of single archives from CLI (e.g., `--file my_report.zip`)
- Export views to CSV/JSON
- Web UI for drill‑downs

---

## License

MIT
