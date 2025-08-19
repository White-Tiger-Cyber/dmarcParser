import os, argparse, sys
from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm
from . import ingest, views
from .repl import run_shell

def _db_path_for(client_key):
    base = os.path.expanduser("~/.dmarcParser/clients")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{client_key}.db")

def main(argv=None):
    argv = argv or sys.argv[1:]
    console = Console()

    ap = argparse.ArgumentParser(prog="dP", description="DMARC Parser")
    sub = ap.add_subparsers(dest="cmd")

    ing = sub.add_parser("ingest", help="Ingest files from a path (default if first arg is a path)")
    ing.add_argument("path", help="File or directory to process")
    ing.add_argument("--client", help="Client key (enables per-client DB & incremental folder runs)")
    ing.add_argument("--state-db", dest="state_db", help="Override path to client DB")
    ing.add_argument("--dry-run", action="store_true", help="Do not write DB; just show what would parse/skip")
    ing.add_argument("--rescan", action="store_true", help="Ignore file mtime/size cache and re-check contents")

    summ = sub.add_parser("summary", help="Show high-level summary from client DB")
    summ.add_argument("--client", required=True, help="Client key")
    summ.add_argument("--days", type=int, help="Restrict to last N days")

    dom = sub.add_parser("domains", help="Aggregate by header_from domain")
    dom.add_argument("--client", required=True, help="Client key")
    dom.add_argument("--limit", type=int, default=25)
    dom.add_argument("--sort", choices=["fail_rate","fails","msgs"], default="fail_rate")
    dom.add_argument("--days", type=int, help="Restrict to last N days")
    dom.add_argument("--fail-only", action="store_true")

    ips = sub.add_parser("ips", help="Aggregate by source IP")
    ips.add_argument("--client", required=True, help="Client key")
    ips.add_argument("--limit", type=int, default=50)
    ips.add_argument("--days", type=int, help="Restrict to last N days")
    ips.add_argument("--failed-only", action="store_true")
    ips.add_argument("--min-fails", type=int, default=1)
    ips.add_argument("--sort", choices=["fails","msgs","fail_rate"], default="fails")

    shell = sub.add_parser("shell", help="Interactive REPL (optionally ingest a path first)")
    # NOTE: --client is now OPTIONAL here
    shell.add_argument("--client", help="Client key (optional; choose in REPL if omitted)")
    shell.add_argument("--path", help="Optional path to ingest before entering shell")
    shell.add_argument("--rescan", action="store_true")

    if argv and not argv[0].startswith("-") and os.path.exists(argv[0]):
        args = ing.parse_args(argv); cmd = "ingest"
    else:
        args = ap.parse_args(argv); cmd = args.cmd

    console = Console()

    if argv and not argv[0].startswith("-") and os.path.exists(argv[0]):
        args = ing.parse_args(argv); cmd = "ingest"
    elif not argv:
        # open shell with no client pre-selected
        run_shell(db_path=None, client_key=None, pending_ingest=None)
        return 0
    else:
        args = ap.parse_args(argv); cmd = args.cmd

    if cmd == "ingest":
        # ... unchanged ...
        return 0

    elif cmd in ("summary", "domains"):
        # ... unchanged ...
        return 0

    elif cmd == "ips":
        # ... unchanged ...
        return 0

    elif cmd == "shell":
        # >>> FIX: do NOT precompute DB when args.client is None
        pending = (os.path.abspath(args.path), bool(args.rescan)) if args.path else None
        run_shell(db_path=None, client_key=args.client, pending_ingest=pending)
        return 0

    else:
        ap.print_help(); return 1

if __name__ == "__main__":
    raise SystemExit(main())
