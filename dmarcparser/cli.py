import os, argparse, sys
from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm
from . import ingest, views, store
from .repl import run_shell
from .views import pct_timeline_view, summary_view, domains_view, ips_view
from .banner import dmarc_banner

def _db_path_for(client_key):
    base = os.path.expanduser("~/.dmarcParser/clients")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{client_key}.db")

def main(argv=None):
    argv = argv or sys.argv[1:]
    console = Console()

    ap = argparse.ArgumentParser(prog="dP", description="DMARC Parser")
    sub = ap.add_subparsers(dest="cmd")

    # Optional: create a client non-interactively
    cc = sub.add_parser("client-create", help="Create a client DB with required domain")
    cc.add_argument("name", help="Client key/name")
    cc.add_argument("--domain", "-d", required=True, help="Client domain, e.g. reliableland.com")
    cc.add_argument("--force", action="store_true", help="Overwrite existing empty DB file")

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

    ips = sub.add_parser(
        "ips",
        help="Aggregate by source IP (msgs, fails, fail%%, domains, last seen)",
        description=(
            "Lists IPs with message and failure counts. Use --auth to include SPF/DKIM breakdown and DMARC disposition counts."
        ),
    )
    ips.add_argument("--client", required=True, help="Client key (database name)")
    ips.add_argument("--days", type=int, help="Days to include (cutoff from latest report end_ts)")
    ips.add_argument("--limit", type=int, default=50, help="Max rows (default: 50)")
    ips.add_argument("--failed-only", action="store_true", help="Only show IPs with failures")
    ips.add_argument("--min-fails", type=int, default=0, help="Only show IPs with at least this many fails (default: 0)")
    ips.add_argument("--sort", choices=["fails", "msgs", "fail_rate"], default="fails", help="Sort key (default: fails)")
    ips.add_argument("--auth", action="store_true", help="Add SPF≠pass, DKIM≠pass, Both≠pass, and DMARC disposition columns")

    pct = sub.add_parser(
        "pct-timeline",
        help="Daily msgs/fail%% vs observed DMARC pct",
        description=(
            "Shows per-day totals with estimated fail rate and the observed DMARC pct reported by receivers "
            "(average with [min–max] since different receivers may honor different pct values on the same day)."
        ),
    )
    pct.add_argument("--client", required=True, help="Client key (database name)")
    pct.add_argument("--days", type=int, default=30, help="How many days back to include (default: 30)")

    shell = sub.add_parser("shell", help="Interactive REPL (choose client or skip chooser with --client)")
    # If provided, --client will open the REPL directly on that client; otherwise you'll get the chooser.
    shell.add_argument("--client", help="Client key (skip chooser if provided)")

    # --- dispatch (prefer known subcommands over "path means ingest") ---
    KNOWN = {"ingest", "summary", "domains", "ips", "pct-timeline", "shell"}
    if not argv:
        # No args -> open interactive shell with chooser
        run_shell(db_path=None, client_key=None, pending_ingest=None)
        return 0
    first = argv[0]
    if first in KNOWN:
        args = ap.parse_args(argv); cmd = args.cmd
    elif (not first.startswith("-")) and os.path.exists(first):
        # Treat first-arg path as 'ingest' only when no subcommand is present
        args = ing.parse_args(argv); cmd = "ingest"
    else:
        args = ap.parse_args(argv); cmd = args.cmd
    # --------------------------------------------------------------------

    if cmd == "ingest":
        try:
            # ... unchanged ...
            return 0
        except KeyboardInterrupt:
            console.print("\n[red]^C[/red] ingest interrupted")
            return 130

    elif cmd == "summary":
        try:
            db = _db_path_for(args.client)
            summary_view(db, days=args.days)
            return 0
        except KeyboardInterrupt:
            console.print("\n[red]^C[/red] summary interrupted")
            return 130

    elif cmd == "domains":
        try:
            db = _db_path_for(args.client)
            domains_view(
                db,
                limit=args.limit,
                days=args.days,
                fail_only=args.fail_only,   # note: flag name is --fail-only
                sort=args.sort,
            )
            return 0
        except KeyboardInterrupt:
            console.print("\n[red]^C[/red] domains interrupted")
            return 130

    elif cmd == "pct-timeline":
        try:
            db = _db_path_for(args.client)
            pct_timeline_view(db, days=args.days)
            return 0
        except KeyboardInterrupt:
            console.print("\n[red]^C[/red] pct-timeline interrupted")
            return 130

    elif cmd == "ips":
        try:
            db = _db_path_for(args.client)
            ips_view(
                db,
                limit=args.limit,
                days=args.days,
                failed_only=args.failed_only,
                min_fails=args.min_fails,
                sort=args.sort,
                auth_breakdown=args.auth,
            )
            return 0
        except KeyboardInterrupt:
            console.print("\n[red]^C[/red] ips interrupted")
            return 130

    elif cmd == "shell":
        # If --client is provided, open the REPL directly on that client (skip chooser)
        try:
            dbp = _db_path_for(args.client) if args.client else None
            run_shell(db_path=dbp, client_key=args.client, pending_ingest=None)
            return 0
        except KeyboardInterrupt:
            console.print("\n[red]^C[/red] shell interrupted")
            return 130

    else:
        ap.print_help(); return 1

if __name__ == "__main__":
    raise SystemExit(main())
