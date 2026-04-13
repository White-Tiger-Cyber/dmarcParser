# dmarcParser

**Automated DMARC aggregate (RUA) report ingestion, analysis, and monitoring.**

Ingest reports from local files *or* automatically from a Gmail inbox. Visualize results in a local web dashboard or the terminal. Built to turn raw DMARC XML into actionable decisions — hunt impersonators, fix deliverability gaps, and validate policy rollouts.

---

## The story (real test case, anonymized)

We built this tool to move beyond eyeballing XML. On our first real-world test, it immediately surfaced insights that would have been painful to see otherwise:

- We could list **every source IP sending as the domain** and see which ones **failed DMARC** (and why).
- That spotlighted a **malicious actor** attempting to impersonate the domain from multiple IPs.
- It also revealed a set of **legitimate outbound systems** that were failing DMARC because their IPs weren't covered by SPF.
- With those facts, we blocked the attacker and **updated SPF** to include the missing infrastructure, which **improved deliverability**.

The value wasn't "DMARC failed somewhere." It was **"these exact IPs failed this way on these days,"** which turned guessing into action.

---

## Features

- **Automated inbox agent** — polls a Gmail inbox for DMARC report emails, extracts attachments, and auto-routes them to the correct per-client database
- **Auto-client creation** — new policy domains are registered automatically; no manual setup needed
- **Local web dashboard** — dark-themed Bootstrap 5 UI with filterable views for summary, domains, IPs, and PCT timeline
- **CLI & REPL** — ingest from local folders/files, query from the terminal, automate via scripts
- **Duplicate suppression** — SHA-256 content hash prevents re-ingesting the same report twice
- **Per-client SQLite databases** — lightweight, local, portable
- **SPF/DKIM/DMARC auth breakdown** — per-IP disposition and authentication detail views
- **PCT timeline** — validate policy rollouts as you dial `pct` from 0 to 100

---

## Architecture

```
Gmail inbox (dmarcparser@yourdomain.com)
        |
        v
  dP agent (polling daemon)
        |
        |-- extract attachment bytes
        |-- parse domain from filename
        |-- ensure_client_for_domain()  -->  ~/.dmarcParser/index.db
        |-- ingest_bytes()              -->  ~/.dmarcParser/clients/<domain>.db
        |-- apply label: dmarc-processed
        |-- mark as read
        v
  dP serve  -->  http://localhost:5000
```

---

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install .
```

This exposes the console entrypoint **`dP`**.

---

## Prerequisites for the Gmail agent

The agent uses the Gmail API with a Google Workspace service account and domain-wide delegation. One-time setup:

1. **Create a dedicated inbox** — e.g. `dmarcparser@yourdomain.com` in Google Workspace
2. **Update your DMARC DNS record** — add `rua=mailto:dmarcparser@yourdomain.com` to each domain's `_dmarc` TXT record
3. **Create a GCP project** and enable the Gmail API
4. **Create a service account** and download the JSON key to `~/.dmarcParser/credentials/`
5. **Grant domain-wide delegation** — in Google Workspace Admin, authorize the service account with scope `https://www.googleapis.com/auth/gmail.modify`
6. **Create a `.env` file** in your project root:

```env
ANTHROPIC_API_KEY=sk-ant-...   # optional, reserved for future Claude edge-case handling
```

7. **Configure** `~/.dmarcParser/config.toml` (or `config.yaml`):

```toml
[gmail]
delegated_user = "dmarcparser@yourdomain.com"

[agent]
poll_interval = 15   # minutes
verbosity = "structured"
```

---

## Usage

### Automated agent (recommended)

```bash
# Run continuously, polling every 15 minutes
dP agent

# Process inbox once and exit (drains all batches)
dP agent --once

# Options
dP agent --interval 5          # poll every 5 minutes
dP agent --verbosity full      # show all detail
dP agent --verbosity quiet     # totals only
dP agent --dry-run             # identify reports but don't ingest or label
```

### Web dashboard

```bash
dP serve                       # http://localhost:5000
dP serve --port 8080
```

Dashboard views per client:
- **Summary** — messages, fail rate, reports, unique IPs/domains, date range
- **Domains** — aggregate by `header_from` with fail rate badges; filter by days/sort/limit/fails-only
- **IPs** — aggregate by source IP; enable **Auth breakdown** for SPF/DKIM/disposition columns
- **PCT Timeline** — daily msgs, fail%, and observed `pct` range

### CLI one-liners

```bash
# Ingest from local files
dP ingest /path/to/rua/folder --client example.com
dP ingest /path/to/rua/folder --client example.com --rescan

# Terminal views
dP summary --client example.com --days 30
dP domains --client example.com --days 30 --sort fail_rate --fail-only
dP ips     --client example.com --days 30 --auth --sort fails
dP pct-timeline --client example.com --days 45

# List all known clients
dP clients
```

### Interactive REPL

```bash
dP shell
# or jump straight into a client:
dP shell --client example.com
```

```text
dP> ingest /path/to/rua/folder
dP> summary
dP> domains
dP> ips --auth
dP> pct-timeline --days 30
```

---

## What the views tell you

- **`summary`** — total messages, estimated fails, rough fail rate, distinct `header_from` domains, distinct source IPs, date range.
- **`domains`** — aggregates by `header_from` with message count, failures, and fail rate; filter with `--fail-only`.
- **`ips`** — aggregates by `source_ip` with messages, failures, fail%, unique domains seen, and last seen date.
  Add `--auth` to include columns for **SPF fail**, **DKIM fail**, **Both fail**, and **DMARC disposition** (reject/quarantine/none).
- **`pct-timeline`** — per-day message totals, estimated fail rate, and an **observed `pct`** (average with min-max across receivers). Helps verify policy rollouts like `p=quarantine; pct=10`.

> **Failure definition:** a message is counted as a DMARC failure if the receiver's disposition is `reject`/`quarantine` **or** both SPF and DKIM are not `pass`. Counts and percentages are based on DMARC's per-record `count` field.

---

## Data model & storage

```
~/.dmarcParser/
  index.db                  # shared: clients registry + processed_emails audit log
  credentials/              # GCP service account JSON key(s)
  clients/
    example.com.db          # per-client SQLite DB (auto-created)
    otherdomain.com.db
```

**index.db tables:**
- `clients` — maps domain -> db_path, created_at
- `processed_emails` — Gmail message_id, status (ingested/skipped/error), notes

**Per-client DB tables:**
- `reports` — one row per feedback report (org, report_id, policy, window start/end)
- `records` — expanded rows per DMARC record (`source_ip`, `header_from`, `spf_result`, `dkim_result`, `disposition`, `count`, `day`)
- `files_seen` — ingestion deduplication (path, size, mtime, SHA-256)
- `ingest_sessions` — audit log of each ingest run

---

## Typical workflows

- **Hunt impersonators** — run `ips --auth` and sort by fails; pivot to domains for context.
- **Fix deliverability** — find legitimate senders failing SPF; update SPF records and re-ingest to verify.
- **Policy rollout validation** — use `pct-timeline` to confirm receivers honor your `pct` as you dial from 0 to 100 and eventually move from `p=none` to `p=quarantine` to `p=reject`.
- **Multi-client monitoring** — run `dP agent` as a daemon; the dashboard homepage shows all clients with color-coded fail rates at a glance.

---

## Requirements

- Python **3.9+**
- For Gmail agent: Google Workspace account, GCP service account with Gmail API enabled
- Packages installed automatically via `pip install .`:
  - `rich`, `flask`, `python-dotenv`
  - `google-api-python-client`, `google-auth-httplib2`, `google-auth-oauthlib`
  - `anthropic` (reserved for future edge-case handling)

---

## Contributing

Issues and PRs welcome. This project was built to be practical: small, fast, and focused on **actionable** visibility.

---

## License

MIT
