import os, argparse, sys
from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm
from . import ingest, views, store
from .repl import run_shell
from .views import pct_timeline_view, summary_view, ips_view, senders_view, reporters_view
from .banner import dmarc_banner
from .config import load_config

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

    snd = sub.add_parser("senders", help="Aggregate by SPF sending domain")
    snd.add_argument("--client", required=True, help="Client key")
    snd.add_argument("--limit", type=int, default=50)
    snd.add_argument("--sort", choices=["fails","msgs","fail_rate","forwarded"], default="fails")
    snd.add_argument("--days", type=int, help="Restrict to last N days")
    snd.add_argument("--fail-only", action="store_true")

    rep = sub.add_parser("reporters", help="Aggregate by reporting organization")
    rep.add_argument("--client", required=True, help="Client key")

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
    shell.add_argument("--client", help="Client key (skip chooser if provided)")

    # ----- agent -----
    ag = sub.add_parser("agent", help="Monitor DMARC inbox and ingest reports via Claude agent")
    ag.add_argument("--interval", type=int, default=15,
                    help="Poll interval in minutes (default: 15); ignored with --once")
    ag.add_argument("--once", action="store_true",
                    help="Process current inbox contents and exit (do not loop)")
    ag.add_argument("--dry-run", action="store_true",
                    help="Identify reports but do not ingest or label emails")
    ag.add_argument("--verbosity", choices=["quiet", "structured", "full"],
                    default="structured",
                    help="Output level: quiet | structured (default) | full")

    # ----- serve -----
    sv = sub.add_parser("serve", help="Start local Flask dashboard at http://localhost:PORT")
    sv.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    sv.add_argument("--client", help="Limit dashboard to one client (default: all)")

    # --- dispatch (prefer known subcommands over "path means ingest") ---
    KNOWN = {"ingest", "summary", "senders", "reporters", "ips", "pct-timeline", "shell", "agent", "serve"}
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

    elif cmd == "senders":
        try:
            db = _db_path_for(args.client)
            senders_view(db, limit=args.limit, days=args.days,
                         fail_only=args.fail_only, sort=args.sort)
            return 0
        except KeyboardInterrupt:
            console.print("\n[red]^C[/red] senders interrupted")
            return 130

    elif cmd == "reporters":
        try:
            db = _db_path_for(args.client)
            reporters_view(db)
            return 0
        except KeyboardInterrupt:
            console.print("\n[red]^C[/red] reporters interrupted")
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

    elif cmd == "agent":
        try:
            from . import gmail_client as gc
            from . import agent as ag_mod
            cfg = load_config()
            svc = gc.build_service(
                delegated_user=cfg["gmail"]["delegated_user"]
            )
            idx = cfg["paths"]["index_db"]
            if args.once:
                console.print("[cyan]Draining inbox...[/cyan]")
                totals = {"ingested": 0, "skipped": 0, "errors": 0}
                batch = 0
                while True:
                    remaining = gc.list_unprocessed_messages(svc)
                    if not remaining:
                        break
                    batch += 1
                    console.print(f"[dim]Batch {batch} — {len(remaining)} message(s)[/dim]")
                    summary = ag_mod.run_agent_once(
                        svc,
                        index_db_path=idx,
                        dry_run=args.dry_run,
                        verbosity=args.verbosity,
                    )
                    totals["ingested"] += summary["ingested"]
                    totals["skipped"]  += summary["skipped"]
                    totals["errors"]   += summary["errors"]
                console.print(
                    f"[green]Done.[/green]  "
                    f"ingested={totals['ingested']}  "
                    f"skipped={totals['skipped']}  "
                    f"errors={totals['errors']}  "
                    f"({batch} batch{'es' if batch != 1 else ''})"
                )
            else:
                ag_mod.run_agent_loop(
                    svc,
                    index_db_path=idx,
                    interval=args.interval,
                    dry_run=args.dry_run,
                    verbosity=args.verbosity,
                )
            return 0
        except KeyboardInterrupt:
            console.print("\n[red]^C[/red] agent interrupted")
            return 130

    elif cmd == "serve":
        try:
            import webbrowser
            from .webapp.app import create_app
            cfg = load_config()
            app = create_app(
                index_db_path=cfg["paths"]["index_db"],
                filter_client=args.client,
            )
            url = f"http://localhost:{args.port}"
            console.print(f"[cyan]Starting dashboard at {url}[/cyan]")
            webbrowser.open(url)
            app.run(host="127.0.0.1", port=args.port, debug=False)
            return 0
        except KeyboardInterrupt:
            console.print("\n[red]^C[/red] serve interrupted")
            return 130

    else:
        ap.print_help(); return 1

if __name__ == "__main__":
    raise SystemExit(main())
