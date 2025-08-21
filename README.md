# dmarcParser

**Interactive CLI + REPL for ingesting and analyzing DMARC aggregate (RUA) reports.**  
See exactly which IPs and senders *pass* or *fail* SPF/DKIM/DMARC, hunt impersonators, and fix deliverability.

---

## The story (real test case, anonymized)

We built this tool to move beyond eyeballing XML. On our first real-world test, it immediately surfaced insights that would have been painful to see otherwise:

- We could list **every source IP sending as the domain** and see which ones **failed DMARC** (and why).  
- That spotlighted a **malicious actor** attempting to impersonate the domain from multiple IPs.  
- It also revealed a set of **legitimate outbound systems** that were failing DMARC because their IPs weren’t covered by SPF.  
- With those facts, we blocked the attacker and **updated SPF** to include the missing infrastructure, which **improved deliverability**.

The value wasn’t “DMARC failed somewhere.” It was **“these exact IPs failed this way on these days,”** which turned guessing into action.

---

## Features

- **Ingest** DMARC RUA reports from folders or files (`.xml`, `.gz`, `.zip`) with duplicate suppression
- Store results in a **per‑client SQLite DB** for incremental analysis
- **REPL shell** with history and quick commands (`summary`, `domains`, `ips`, `pct-timeline`)
- **CLI subcommands** for automation and one‑liners
- IP‑centric and domain‑centric views, with optional **SPF/DKIM/DMARC breakdown**
- Lightweight, fast, and local—no external services

---

## Install

Create a virtual environment, then install the package (editable installs work too):

````bash
python3 -m venv .venv
source .venv/bin/activate
pip install .
````

This exposes the console entrypoint **`dP`**.

---

## Quick start

### Option A — jump straight into the REPL

````bash
dP
````

You’ll be prompted to pick (or create) a **client** database, e.g.:

````text
Available clients: AcmeCo, ExampleCorp
Client to use (or new name) :
````

Then ingest and explore:

````text
dP>: ingest /path/to/rua/folder
dP>: summary
dP>: domains
dP>: ips --auth
dP>: pct-timeline --days 30
````

### Option B — one‑liners with the CLI

You can also use subcommands directly (good for automation/CI):

## Usage

```bash
dP [command] [options]

Commands:
  ingest              Ingest files from a path (default if first arg is a path)
  summary             Show high-level summary from client DB
  domains             Aggregate by header_from domain
  ips                 Aggregate by source IP (msgs, fails, fail%, domains, last seen)
  pct-timeline        Daily msgs/fail% vs observed DMARC pct
  shell               Interactive REPL (optionally ingest a path first).
                      Supports `--client` to start directly in a specific client DB.

Options:
  -h, --help          Show this help message and exit
  --client CLIENT     Specify client name to use for the command,
                      skipping the interactive client selection prompt.
```

````bash
# Ingest
dP ingest /path/to/rua/folder --client ExampleCorp --rescan

# High-level stats
dP summary --client ExampleCorp --days 30

# Domain aggregation
dP domains --client ExampleCorp --days 30 --limit 50 --sort fail_rate --fail-only

# IP aggregation (+ SPF/DKIM/DMARC breakdown columns)
dP ips --client ExampleCorp --days 30 --auth --sort fails --limit 50

# Daily fail% vs observed DMARC pct
dP pct-timeline --client ExampleCorp --days 45
````

---

## What the views tell you

- **`summary`** – total messages, estimated fails, rough fail rate, distinct header_from domains, distinct source IPs, date range.
- **`domains`** – aggregates by `header_from` with message count, failures, and fail‑rate; filter with `--fail-only`.
- **`ips`** – aggregates by `source_ip` with messages, failures, fail%, unique domains seen, and last seen date.  
  Add `--auth` to include columns for **SPF fail**, **DKIM fail**, **Both fail**, and **DMARC disposition** (reject/quarantine/none).
- **`pct-timeline`** – per‑day message totals, estimated fail rate, and an **observed `pct`** (average with min–max across receivers). This helps verify policy rollouts like `p=quarantine; pct=10`.

> **Failure definition (in views):** a message is counted as a DMARC failure if the receiver’s disposition is `reject`/`quarantine` **or** both SPF and DKIM are not `pass`. Counts and percentages are based on DMARC’s per‑record `count` field.

---

## Data model & storage

- Each client’s data lives in **`~/.dmarcParser/clients/<client>.db`** (SQLite).  
- A simple file memo (mtime/size) avoids re‑parsing unchanged files; use `--rescan` to force checks.  
- Tables:
  - `reports` – one row per feedback report (metadata, window start/end)
  - `records` – expanded rows per DMARC record (`source_ip`, `header_from`, `spf_result`, `dkim_result`, `disposition`, `count`, `day`)
  - `files_seen` / `ingest_sessions` – ingestion bookkeeping

---

## Typical workflows

- **Hunt impersonators:** run `ips --auth` and filter/sort to find IPs with high fail counts; pivot to domains.  
- **Fix deliverability:** use `domains` and `ips --auth` to find legitimate senders failing due to SPF gaps; update SPF and re‑ingest to verify.  
- **Policy rollout validation:** `pct-timeline` shows if mailbox providers are honoring your `pct` as you dial from 0→100.

---

## Requirements

- Python **3.9+**
- Packages: `rich`, `tabulate` (installed via `pip install .`)

---

## Contributing

Issues and PRs welcome. This project was built to be practical: small, fast, and focused on **actionable** visibility.

---

## License

MIT
