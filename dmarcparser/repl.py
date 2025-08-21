import sqlite3,os, shlex
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.markup import escape
from rich.table import Table
from . import views, ingest
from .views import pct_timeline_view, summary_view, domains_view, ips_view

CLIENTS_DIR = os.path.expanduser("~/.dmarcParser/clients")
HIST_DIR    = os.path.expanduser("~/.dmarcParser/history")

def _list_clients():
    os.makedirs(CLIENTS_DIR, exist_ok=True)
    out = []
    for n in sorted(os.listdir(CLIENTS_DIR)):
        if n.endswith(".db"):
            out.append(n[:-3])
    return out

def _db_path(client_key):
    os.makedirs(CLIENTS_DIR, exist_ok=True)
    return os.path.join(CLIENTS_DIR, f"{client_key}.db")

def _setup_history(client_key: str):
    try:
        import readline
    except Exception:
        return None
    os.makedirs(HIST_DIR, exist_ok=True)
    hist_file = os.path.join(HIST_DIR, f"{client_key}.hist")
    try:
        readline.read_history_file(hist_file)
    except FileNotFoundError:
        pass
    readline.set_history_length(50)
    return hist_file

def _save_history(hist_file):
    try:
        import readline
        if hist_file:
            readline.write_history_file(hist_file)
    except Exception:
        pass

def _choose_client(console: Console):
    """Interactive chooser that tolerates 'client <name>' and supports re-listing."""
    while True:
        clients = _list_clients()
        if clients:
            console.print("[bold]Available clients:[/bold] " + ", ".join(clients))
        else:
            console.print("[yellow]No clients yet.[/yellow]")

        raw = Prompt.ask("Client to use (or new name) [type 'list' to re-list]").strip()
        if not raw:
            console.print("[red]Please enter a client name.[/red]")
            continue

        # allow people to type 'client demo' out of habit
        if raw.lower().startswith("client "):
            raw = raw.split(None, 1)[1].strip()

        if raw.lower() in {"list", "ls", "clients"}:
            continue

        # mild guardrails against accidental names
        if raw.lower() in {"client", "clients"}:
            console.print("[red]That looks like a command, not a name.[/red]")
            continue

        return raw

def _resolve_db_or_reprompt(console: Console, client_key: str):
    """
    Given a client key, return an existing/created DB path.
    If the user declines creation, return None so the caller can re-prompt.
    """
    db_path = _db_path(client_key)
    if os.path.exists(db_path):
        return db_path

    create = Confirm.ask(f"No DB for '{client_key}'. Create at {db_path}?", default=True)
    if create:
        # Ensure parent dir exists; initializer can be a no-op if your DDL runs lazily.
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        # If you have an explicit initializer, call it here:
        # store.init_db(db_path)
        open(db_path, "a").close()
        return db_path

    console.print("[yellow]Returning to client selection...[/yellow]\n")
    return None

def _last_root_path(db_path):
    """Return the most recent root_path used for this client's DB, or None."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute("SELECT root_path FROM ingest_sessions ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None

def run_shell(db_path=None, client_key=None, pending_ingest=None):
    console = Console()

    # initial client selection loop
    while not db_path:
        client_key = _choose_client(console)
        resolved = _resolve_db_or_reprompt(console, client_key)
        if resolved:
            db_path = resolved
            break
        # else: user said "no" → loop back to choose again

    db_path = _db_path(client_key)
    if not os.path.exists(db_path):
        if Confirm.ask(f"No DB for '{client_key}'. Create at {db_path}?", default=True):
            open(db_path, "ab").close()
        else:
            console.print("[yellow]Aborted.[/yellow]")
            return

    # optional one-shot ingest on entry (from CLI)
    if pending_ingest:
        p, rescan = pending_ingest
        console.print(f"Ingesting initial path: {p}")
        ingest.ingest(p, db_path, client_key, rescan=rescan, dry_run=False)

    # history per client
    hist_file = _setup_history(client_key)

    def _print_banner():
        console.print(f"[bold]dmarcParser[/bold] — client: [cyan]{client_key}[/cyan]  DB: {db_path}")
        console.print(
            "Commands: clients | client <name> | summary \\[--days N\\] | "
            "domains \\[--limit N\\] \\[--sort msgs|fails|fail_rate\\] \\[--days N\\] \\[--fail-only\\] | "
            "ips \\[--failed-only\\] \\[--min-fails N\\] \\[--days N\\] \\[--limit N\\] \\[--sort fails|msgs|fail_rate\\] \\[--auth\\] | "
            "pct-timeline \\[--days N\\] | ingest <path> \\[--rescan\\] | rescan \\[--path DIR\\] \\[--dry-run\\] | "
            "paths | clear | help | exit"
        )

    _print_banner()

    try:
        while True:
            try:
                line = Prompt.ask("dP>")
            except (EOFError, KeyboardInterrupt):
                console.print("")
                break

            if not line.strip():
                continue

            try:
                parts = shlex.split(line)
            except Exception as e:
                console.print(f"[red]Parse error:[/red] {e}")
                continue

            cmd = parts[0].lower()

            if cmd in ("exit", "quit", "q"):
                break

            elif cmd == "help":
                # keep the first few lines as you had them...
                console.print("[bold]clients[/bold] – list known client databases")
                console.print("[bold]client <name>[/bold] – switch to (or create) a client DB")
                console.print("[bold]paths[/bold] – show directories for DB and history")
                console.print("[bold]ingest <path>[/bold] [--rescan] – ingest DMARC XMLs from a directory (recurses; .xml/.gz/.zip)")
                console.print("[bold]rescan[/bold] [--path DIR] [--dry-run] – re-evaluate ingest decisions on a path")
                console.print("[bold]summary[/bold] [--days N] – totals, distincts, fail%%, and date range (window-aware)")
                # for lines with bracketed options: bold label + plain options
                console.print("[bold]domains[/bold] ", end="")
                console.print("[--days N] [--limit N] [--sort msgs|fails|fail_rate] [--fail-only] — aggregate by header_from", markup=False)

                console.print("[bold]ips[/bold] ", end="")
                console.print("[--days N] [--limit N] [--sort fails|msgs|fail_rate] [--failed-only] [--min-fails N] [--auth] — aggregate by IP; --auth adds SPF=pass, DKIM=pass, Both=pass, DMARC disposition", markup=False)

                console.print("[bold]pct-timeline[/bold] ", end="")
                console.print("[--days N] — daily msgs, fails, fail%% with observed DMARC pct (avg [min–max])", markup=False)

                console.print("[bold]clear[/bold] – clear the screen")
                console.print("[bold]exit[/bold] | [bold]quit[/bold] – leave the shell")
                continue

            elif cmd == "clients":
                cs = _list_clients()
                console.print("[bold]Clients:[/bold] " + (", ".join(cs) if cs else "(none)"))

            elif cmd == "client":
                if len(parts) < 2:
                    console.print("[red]Usage:[/red] client <name>")
                    continue
                # save current hist
                _save_history(hist_file)
                # switch
                new = parts[1].strip()
                new_db = _db_path(new)
                if not os.path.exists(new_db):
                    if not Confirm.ask(f"Create DB for '{new}' at {new_db}?", default=True):
                        continue
                    open(new_db, "ab").close()
                # swap state + history
                client_key = new
                db_path = new_db
                hist_file = _setup_history(client_key)
                console.print(f"[green]Switched to:[/green] {client_key}  DB: {db_path}")

            elif cmd == "summary":
                days = None
                if "--days" in parts:
                    try:
                        days = int(parts[parts.index("--days")+1])
                    except Exception:
                        console.print("[red]Usage:[/red] summary --days <N>")
                        continue
                views.summary_view(db_path, days=days)

            elif cmd == "domains":
                limit = 25; sort = "fail_rate"; days = None; fail_only = False
                it = iter(parts[1:])
                for tok in it:
                    if tok == "--limit":   limit = int(next(it))
                    elif tok == "--sort":  sort  = next(it)
                    elif tok == "--days":  days  = int(next(it))
                    elif tok == "--fail-only": fail_only = True
                views.domains_view(db_path, limit=limit, sort=sort, days=days, fail_only=fail_only)

            elif cmd == "ips":
                # defaults
                limit = 50
                days = None
                failed_only = False
                min_fails = 1
                sort = "fails"
                auth = False  # new: show SPF/DKIM/DMARC breakdown

                # safe iterator even when no args
                it = iter(parts[1:]) if len(parts) > 1 else iter(())

                for tok in it:
                    if tok == "--days":
                        try:
                            days = int(next(it))
                        except StopIteration:
                            console.print("[red]Usage:[/red] ips [--days N] [--limit N] [--failed-only] [--min-fails N] [--sort fails|msgs|fail_rate] [--auth]")
                            continue
                    elif tok == "--limit":
                        try:
                            limit = int(next(it))
                        except StopIteration:
                            console.print("[red]Usage:[/red] ips --limit N")
                            continue
                    elif tok == "--failed-only":
                        failed_only = True
                    elif tok == "--min-fails":
                        try:
                            min_fails = int(next(it))
                        except StopIteration:
                            console.print("[red]Usage:[/red] ips --min-fails N")
                            continue
                    elif tok == "--sort":
                        try:
                            sort = next(it)
                        except StopIteration:
                            console.print("[red]Usage:[/red] ips --sort fails|msgs|fail_rate")
                            continue
                    elif tok == "--auth":
                        auth = True
                    else:
                        console.print(f"[yellow]Unknown option ignored:[/yellow] {tok}")

                if not db_path:
                    console.print("[red]No client DB selected. Use 'client <name>' first.[/red]")
                    continue

                try:
                    ips_view(
                        db_path,
                        limit=limit,
                        days=days,
                        failed_only=failed_only,
                        min_fails=min_fails,
                        sort=sort,
                        auth_breakdown=auth,   # <- wires the new breakdown flag
                    )
                except Exception as e:
                    console.print(f"[red]ips error:[/red] {e}")
                continue

            elif cmd == "ingest":
                if len(parts) < 2:
                    console.print("[red]Usage:[/red] ingest <path> [--rescan]")
                    continue
                p = os.path.abspath(parts[1]); rescan = "--rescan" in parts[2:]
                console.print(f"Ingesting: {p} (rescan={rescan})")
                result = ingest.ingest(p, db_path, client_key, rescan=rescan, dry_run=False)
                console.print(f"[green]Done.[/green] Decisions: {result['summary']}")

            elif cmd == "clear":
                # destructive: double confirm
                msg = (
                    f"Are you absolutely certain you want to remove all data for client '{client_key}'?\n"
                    f"{escape(db_path)}\n"
                    "This action CANNOT be undone."
                )
                if not Confirm.ask(msg, default=False):
                    console.print("[yellow]Aborted.[/yellow]")
                    continue

                # second confirmation: require an explicit token
                token = Prompt.ask("Type [bold red]DELETE[/bold red] to confirm", default="").strip()
                if token != "DELETE":
                    console.print("[yellow]Aborted.[/yellow]")
                    continue

                try:
                    os.remove(db_path)
                    console.print(f"[green]Deleted DB for '{client_key}'.[/green]")
                except FileNotFoundError:
                    console.print("[yellow]No DB file found to remove.[/yellow]")

                # reset state and choose again
                _save_history(hist_file)
                client_key = _choose_client(console)
                db_path = _db_path(client_key)
                if not os.path.exists(db_path):
                    if Confirm.ask(f"No DB for '{client_key}'. Create at {escape(db_path)}?", default=True):
                        open(db_path, "ab").close()
                    else:
                        console.print("[yellow]Aborted.[/yellow]")
                        break
                hist_file = _setup_history(client_key)
                _print_banner()

            elif cmd == "paths":
                try:
                    conn = sqlite3.connect(db_path)
                    rows = conn.execute("""
                        SELECT root_path, COUNT(*) AS runs
                        FROM ingest_sessions
                        GROUP BY root_path
                        ORDER BY MAX(id) DESC
                    """).fetchall()
                    conn.close()
                except Exception as e:
                    console.print(f"[red]Error reading paths:[/red] {e}")
                    continue

                if not rows:
                    console.print("[yellow]No prior ingest paths recorded for this client.[/yellow]")
                else:
                    t = Table(title="Recent ingest roots")
                    t.add_column("Root Path")
                    t.add_column("Runs", justify="right")
                    for rp, cnt in rows:
                        t.add_row(rp, str(cnt))
                    console.print(t)

            elif cmd == "rescan":
                # usage: rescan [--path DIR] [--dry-run]
                # 1) parse options
                given_path = None
                dry_run = False
                it = iter(parts[1:])
                for tok in it:
                    if tok == "--path":
                        try:
                            given_path = os.path.abspath(next(it))
                        except StopIteration:
                            console.print("[red]Usage:[/red] rescan --path <DIR> [--dry-run]")
                            given_path = None
                            break
                    elif tok == "--dry-run":
                        dry_run = True
                    else:
                        console.print(f"[yellow]Unknown option ignored:[/yellow] {tok}")

                # 2) resolve the path (prefer explicit, else last used)
                target = given_path or _last_root_path(db_path)
                if not target:
                    console.print("[red]No prior ingest path found for this client.[/red] "
                                  "Use: ingest <path> first, or: rescan --path <DIR>")
                    continue

                console.print(f"Rescanning: {target}  (rescan=True, dry_run={dry_run})")
                try:
                    result = ingest.ingest(target, db_path, client_key, rescan=True, dry_run=dry_run)
                except Exception as e:
                    console.print(f"[red]Ingest error:[/red] {e}")
                    continue

                # 3) show summary
                s = result.get("summary") or {}
                t = Table(title="INGEST SUMMARY")
                t.add_column("Decision")
                t.add_column("Count", justify="right")
                for k in sorted(s.keys()):
                    t.add_row(k, str(s[k]))
                console.print(t)

            elif cmd == "pct-timeline":
                # Usage:
                #   pct-timeline
                #   pct-timeline --days 14
                days = 30
                it = iter(parts[1:])
                for tok in it:
                    if tok == "--days":
                        try:
                            days = int(next(it))
                        except StopIteration:
                            console.print("[red]Usage:[/red] pct-timeline [--days N]")
                            continue
                    else:
                        console.print(f"[yellow]Unknown option ignored:[/yellow] {tok}")

                if not db_path:
                    console.print("[red]No client DB selected. Use 'client <name>' first.[/red]")
                    continue

                try:
                    pct_timeline_view(db_path, days=days)
                except Exception as e:
                    console.print(f"[red]pct-timeline error:[/red] {e}")
                continue

            else:
                console.print(f"[red]Unknown command:[/red] {cmd}. Type 'help'.")
    finally:
        _save_history(hist_file)
