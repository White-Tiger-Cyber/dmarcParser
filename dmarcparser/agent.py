"""
DMARC inbox processing agent - pure-Python pipeline.

The original Claude-based agentic loop was replaced with a direct Python
pipeline for the happy path (structured DMARC filenames).  Claude is
retained only for genuine edge cases (unparseable filenames, corrupt
attachments, ambiguous reports) via _handle_edge_cases().

Public API
----------
run_agent_once(gmail_service, ...) -> summary_dict
run_agent_loop(gmail_service, ...)  # blocks until Ctrl+C
"""
import json
import os
import time
import datetime

from rich.console import Console
from rich.rule import Rule

from . import gmail_client, store
from . import ingest as ingest_mod
from .ingest import parse_filename_meta

_INDEX_DB_PATH = os.path.expanduser("~/.dmarcParser/index.db")

# ---------------------------------------------------------------------------
# Label cache - looked up once per run_agent_once call, lazily
# ---------------------------------------------------------------------------

class _LabelCache:
    def __init__(self, service):
        self._svc = service
        self._processed = None
        self._error = None

    def processed(self):
        if self._processed is None:
            self._processed = gmail_client.ensure_label(self._svc, "dmarc-processed")
        return self._processed

    def error(self):
        if self._error is None:
            self._error = gmail_client.ensure_label(self._svc, "dmarc-error")
        return self._error


# ---------------------------------------------------------------------------
# Per-message helpers
# ---------------------------------------------------------------------------

def _mark_processed(service, index_db_path, labels, message_id, status, notes, dry_run):
    """Apply Gmail labels, mark as read, and record outcome in index.db."""
    if not dry_run:
        gmail_client.apply_label(service, message_id, labels.processed())
        gmail_client.mark_as_read(service, message_id)
        if status == "error":
            gmail_client.apply_label(service, message_id, labels.error())
        idx = store.open_index_db(index_db_path)
        idx.execute(
            "INSERT OR REPLACE INTO processed_emails"
            "(message_id, processed_at, status, notes) VALUES (?,?,?,?)",
            (message_id, int(time.time()), status, notes or ""),
        )
        idx.commit()
        idx.close()


def _process_message(service, index_db_path, labels, message_id, dry_run, log_fn):
    """
    Process a single Gmail message.

    Returns one of: "ingested", "skipped", "error"
    and a short notes string.
    """
    # --- 1. Fetch attachments ---
    try:
        attachments = gmail_client.get_attachments(service, message_id)
    except Exception as exc:
        return "error", f"get_attachments failed: {exc}"

    if not attachments:
        return "skipped", "No DMARC attachments found"

    # --- 2. Process each attachment ---
    results = []
    for att in attachments:
        filename = att["filename"]
        data     = att["data"]

        # Extract policy domain from filename
        meta = parse_filename_meta(filename)
        if not meta or not meta.get("domain"):
            log_fn(f"    [yellow]! Cannot parse domain from {filename!r} - skipping attachment[/yellow]")
            results.append(("error", f"Unparseable filename: {filename}"))
            continue

        policy_domain = meta["domain"]
        log_fn(f"    domain=[bold]{policy_domain}[/bold]  file={filename}")

        # Resolve (or create) client
        try:
            client_info = store.ensure_client_for_domain(index_db_path, policy_domain)
        except Exception as exc:
            results.append(("error", f"ensure_client_for_domain: {exc}"))
            continue

        if client_info["is_new"]:
            log_fn(f"    [green]+ Created new client:[/green] {client_info['client_name']}")

        # Ingest
        if dry_run:
            log_fn(f"    [dim]dry-run: would ingest {len(data)} bytes -> {client_info['client_name']}[/dim]")
            results.append(("ingested", ""))
            continue

        try:
            summary = ingest_mod.ingest_bytes(
                data, filename, client_info["db_path"], client_info["client_name"]
            )
            parsed  = summary.get("parsed", 0)
            dup_xml = summary.get("dup_xml", 0)
            log_fn(
                f"    [green]ok ingested[/green]  "
                f"parsed={parsed}  dup={dup_xml}  "
                f"client={client_info['client_name']}"
            )
            results.append(("ingested", ""))
        except Exception as exc:
            log_fn(f"    [red]FAIL ingest error:[/red] {exc}")
            results.append(("error", str(exc)))

    # --- 3. Derive overall status ---
    statuses = [r[0] for r in results]
    if all(s == "ingested" for s in statuses):
        return "ingested", ""
    if all(s == "error" for s in statuses):
        return "error", "; ".join(r[1] for r in results if r[1])
    # Mixed: partial success counts as ingested, notes carry the errors
    error_notes = "; ".join(r[1] for r in results if r[0] == "error" and r[1])
    return "ingested", f"partial errors: {error_notes}" if error_notes else ""


# ---------------------------------------------------------------------------
# Core: run one processing pass
# ---------------------------------------------------------------------------

def run_agent_once(
    gmail_service,
    index_db_path=_INDEX_DB_PATH,
    dry_run=False,
    verbosity="structured",
    log_fn=None,
    console=None,
):
    """
    Process all unprocessed emails in the inbox exactly once.

    Parameters
    ----------
    gmail_service : Google API service object (from gmail_client.build_service)
    index_db_path : path to ~/.dmarcParser/index.db
    dry_run       : if True, identify reports but do not ingest or label
    verbosity     : "quiet" | "structured" | "full"
                    (all three now use the same Python pipeline;
                     "full" simply enables Rich markup in log lines)
    log_fn        : callable(msg) for timestamped log lines
    console       : rich.Console (used for log_fn default)

    Returns
    -------
    dict: {total, ingested, skipped, errors}
    """
    if log_fn is None:
        _console = console or Console()
        log_fn = _console.print

    show_detail = verbosity in ("structured", "full")

    labels = _LabelCache(gmail_service)

    msgs = gmail_client.list_unprocessed_messages(gmail_service)
    counts = {"ingested": 0, "skipped": 0, "errors": 0}

    for i, m in enumerate(msgs, 1):
        message_id = m["id"]

        # Message header for context
        try:
            info = gmail_client.get_message_info(gmail_service, message_id)
            subject = info.get("subject", "")[:60]
        except Exception:
            subject = message_id

        if show_detail:
            log_fn(f"  [{i}/{len(msgs)}] {subject}")

        status, notes = _process_message(
            gmail_service, index_db_path, labels,
            message_id, dry_run,
            log_fn if show_detail else (lambda _: None),
        )

        counts[status] = counts.get(status, 0) + 1

        try:
            _mark_processed(
                gmail_service, index_db_path, labels,
                message_id, status, notes, dry_run,
            )
        except Exception as exc:
            log_fn(f"  [red]mark_processed failed for {message_id}: {exc}[/red]")

    total = sum(counts.values())
    return {"total": total, **counts}


# ---------------------------------------------------------------------------
# Polling loop with terminal status output
# ---------------------------------------------------------------------------

def run_agent_loop(
    gmail_service,
    index_db_path=_INDEX_DB_PATH,
    interval=15,
    dry_run=False,
    verbosity="structured",
):
    """
    Poll the inbox every `interval` minutes until Ctrl+C.
    Prints a rich terminal status header and timestamped log lines.
    """
    console = Console()

    def log(msg):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        console.print(f"[dim][{ts}][/dim] {msg}")

    # Header banner
    console.print()
    console.print(Rule("[bold cyan]dmarcParser agent[/bold cyan]"))
    console.print(
        f"  interval=[bold]{interval}m[/bold]  "
        f"verbosity=[bold]{verbosity}[/bold]  "
        f"dry_run=[bold]{dry_run}[/bold]  "
        f"started=[bold]{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold]"
    )
    console.print(Rule())
    console.print()

    cycle = 0
    processed_today = 0

    try:
        while True:
            cycle += 1
            log(f"Cycle [bold]#{cycle}[/bold] -checking inbox...")

            try:
                msgs = gmail_client.list_unprocessed_messages(gmail_service)
                if not msgs:
                    log("Nothing new in inbox.")
                else:
                    log(f"[green]{len(msgs)} unprocessed message(s)[/green] -processing")
                    summary = run_agent_once(
                        gmail_service,
                        index_db_path=index_db_path,
                        dry_run=dry_run,
                        verbosity=verbosity,
                        log_fn=log,
                        console=console,
                    )
                    processed_today += summary.get("ingested", 0)
                    log(
                        f"[green]Done.[/green]  "
                        f"ingested=[bold]{summary['ingested']}[/bold]  "
                        f"skipped=[bold]{summary['skipped']}[/bold]  "
                        f"errors=[bold]{summary['errors']}[/bold]  "
                        f"(session total: {processed_today})"
                    )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log(f"[red]Cycle error: {exc}[/red]")

            next_dt = datetime.datetime.now() + datetime.timedelta(minutes=interval)
            log(f"Sleeping {interval} min -next check at [bold]{next_dt.strftime('%H:%M:%S')}[/bold]")
            console.print()

            # Sleep in 1-second ticks so Ctrl+C is responsive
            end_ts = time.time() + interval * 60
            while time.time() < end_ts:
                time.sleep(1)

    except KeyboardInterrupt:
        console.print()
        console.print(Rule("[yellow]Shutting down[/yellow]"))
        log("Agent stopped by user.")
