# dmarcParser

Interactive REPL tool for parsing DMARC aggregate (RUA) reports at scale.

## Features
- Ingest DMARC XML from `.xml`, `.gz`, `.zip` (dedup by XML hash)
- Per-client SQLite DB with incremental runs
- REPL shell: `summary`, `domains`, `ips`, `ingest`, `rescan`, `clients`, `client <name>`, `clear`
- Arrow-key history per client (last 50)
- Threat-hunting views: top failing IPs, fail rates, alignment heuristics

## Quick start
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# launch interactive shell (pick client inside)
dP
# or ingest directly
dP ingest /path/to/DMARC --client MyClient --rescan
