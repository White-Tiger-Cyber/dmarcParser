from rich.table import Table
from rich.console import Console
import sqlite3, json, datetime
from collections import defaultdict

def _open(db_path):
    return sqlite3.connect(db_path)

def _date_from_epoch(ts):
    try:
        return datetime.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return None

def summary_view(db_path, days=None):
    """High-level summary: totals, distincts, date range, rough fail rate."""
    conn = _open(db_path)
    c = conn.cursor()

    where = ""
    params = []
    if days:
        c.execute("SELECT MAX(end_ts) FROM reports")
        max_end = c.fetchone()[0]
        if max_end:
            cutoff = int(max_end) - days * 86400
            where = "WHERE end_ts >= ?"
            params = [cutoff]

    # totals
    c.execute(f"SELECT COUNT(*) FROM reports {where}", params)
    reports = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(count),0) FROM records")
    msgs = c.fetchone()[0]

    # date range
    c.execute(f"SELECT MIN(end_ts), MAX(end_ts) FROM reports {where}", params)
    r = c.fetchone()
    start = _date_from_epoch(r[0]) if r and r[0] else None
    end   = _date_from_epoch(r[1]) if r and r[1] else None

    # distincts
    c.execute("SELECT COUNT(DISTINCT header_from) FROM records WHERE header_from IS NOT NULL")
    uniq_domains = c.fetchone()[0]
    c.execute("SELECT COUNT(DISTINCT source_ip) FROM records")
    uniq_ips = c.fetchone()[0]

    # fail estimate: DMARC disposition reject/quarantine OR both SPF/DKIM != pass
    c.execute("""
        SELECT COALESCE(SUM(count),0) FROM records
        WHERE disposition IN ('reject','quarantine')
           OR ((spf_result IS NULL OR LOWER(spf_result)!='pass')
            AND (dkim_result IS NULL OR LOWER(dkim_result)!='pass'))
    """)
    fails = c.fetchone()[0] or 0
    fail_rate = (fails / msgs * 100.0) if msgs else 0.0

    table = Table(title="SUMMARY")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Reports", str(reports))
    table.add_row("Messages (sum count)", str(msgs))
    table.add_row("Estimated Fails", str(fails))
    table.add_row("Estimated Fail %", f"{fail_rate:.1f}%")
    table.add_row("Distinct header_from", str(uniq_domains))
    table.add_row("Distinct source IPs", str(uniq_ips))
    table.add_row("Date range", f"{start or '-'} → {end or '-'}")
    Console().print(table)

def _aligned_guess(header_from, spf_domain, dkim_results_json):
    """Very rough alignment heuristic: SPF domain == header_from OR any DKIM pass for that domain."""
    try:
        if header_from and spf_domain and header_from.lower() == (spf_domain or "").lower():
            return True
        for d in json.loads(dkim_results_json or "[]"):
            if (d.get("result","").lower() == "pass" and
                header_from and d.get("domain") and header_from.lower() == d["domain"].lower()):
                return True
    except Exception:
        pass
    return False

def domains_view(db_path, limit=25, sort="fail_rate", days=None, fail_only=False):
    """Aggregate by header_from domain with msgs/fails/fail%/aligned%/unique IPs."""
    conn = _open(db_path)
    c = conn.cursor()

    # optional time window (by reports.end_ts)
    join_where = ""
    params = []
    if days:
        c.execute("SELECT MAX(end_ts) FROM reports")
        max_end = c.fetchone()[0]
        if max_end:
            cutoff = int(max_end) - days * 86400
            join_where = "JOIN reports r ON r.fp_report = rec.fp_report WHERE r.end_ts >= ?"
            params = [cutoff]

    rows = c.execute(f"""
        SELECT rec.header_from, rec.source_ip, rec.count, rec.disposition,
               rec.spf_result, rec.spf_domain, rec.dkim_result, rec.dkim_results_json
        FROM records rec
        {join_where}
    """, params).fetchall()

    # aggregate
    agg = {}
    for hfrom, ip, count, disp, spf_res, spf_dom, dkim_res, dkim_json in rows:
        if not hfrom:
            continue
        a = agg.setdefault(hfrom, {"msgs": 0, "fails": 0, "aligned": 0, "ips": set()})
        cnt = count or 0
        a["msgs"] += cnt
        is_fail = (
            (disp in ("reject", "quarantine")) or
            ((not spf_res or spf_res.lower() != "pass") and (not dkim_res or dkim_res.lower() != "pass"))
        )
        if is_fail:
            a["fails"] += cnt
        if _aligned_guess(hfrom, spf_dom, dkim_json):
            a["aligned"] += cnt
        a["ips"].add(ip)

    # format rows
    rows_out = []
    for hfrom, a in agg.items():
        if fail_only and a["fails"] == 0:
            continue
        msgs = a["msgs"]
        fails = a["fails"]
        fail_rate = (fails / msgs * 100.0) if msgs else 0.0
        aligned_rate = ((a["aligned"] / msgs) * 100.0) if msgs else 0.0
        rows_out.append({
            "domain": hfrom,
            "msgs": msgs,
            "fails": fails,
            "fail_rate": fail_rate,
            "aligned_rate": aligned_rate,
            "ips": len(a["ips"]),
        })

    sort_key = {
        "fail_rate": lambda x: (-x["fail_rate"], -x["fails"]),
        "fails":     lambda x: (-x["fails"], -x["msgs"]),
        "msgs":      lambda x: (-x["msgs"], -x["fails"]),
    }.get(sort, lambda x: (-x["fail_rate"], -x["fails"]))
    rows_out.sort(key=sort_key)

    title = f"DOMAINS (top {limit})" if limit else "DOMAINS"
    table = Table(title=title)
    table.add_column("Domain")
    table.add_column("Msgs", justify="right")
    table.add_column("Fails", justify="right")
    table.add_column("Fail%", justify="right")
    table.add_column("Aligned%", justify="right")
    table.add_column("IPs", justify="right")

    for r in rows_out[:limit] if limit else rows_out:
        table.add_row(
            r["domain"],
            str(r["msgs"]),
            str(r["fails"]),
            f"{r['fail_rate']:.1f}%",
            f"{r['aligned_rate']:.1f}%",
            str(r["ips"]),
        )

    Console().print(table)

def ips_view(db_path, limit=50, days=None, failed_only=False, min_fails=1, sort="fails"):
    """
    Aggregate by source_ip.
    Columns: IP, msgs, fails, fail%, unique header_from domains, last_seen (UTC date).
    Failure definition: disposition in {reject, quarantine} OR (spf!=pass AND dkim!=pass).
    """
    conn = _open(db_path)
    c = conn.cursor()

    join_where = ""
    params = []
    if days:
        c.execute("SELECT MAX(end_ts) FROM reports")
        max_end = c.fetchone()[0]
        if max_end:
            cutoff = int(max_end) - days * 86400
            join_where = "JOIN reports r ON r.fp_report = rec.fp_report WHERE r.end_ts >= ?"
            params = [cutoff]

    rows = c.execute(f"""
        SELECT rec.source_ip, rec.count, rec.disposition,
               rec.spf_result, rec.dkim_result, rec.header_from, r.end_ts
        FROM records rec
        JOIN reports r ON r.fp_report = rec.fp_report
        {("WHERE r.end_ts >= ?" if days else "")}
    """, params).fetchall()

    agg = {}
    domains_by_ip = defaultdict(set)
    last_seen = {}

    for ip, cnt, disp, spf_res, dkim_res, hfrom, end_ts in rows:
        if not ip:
            continue
        a = agg.setdefault(ip, {"msgs": 0, "fails": 0})
        cnt = cnt or 0
        a["msgs"] += cnt
        is_fail = (
            (disp in ("reject", "quarantine")) or
            ((not spf_res or spf_res.lower() != "pass") and (not dkim_res or dkim_res.lower() != "pass"))
        )
        if is_fail:
            a["fails"] += cnt
        if hfrom:
            domains_by_ip[ip].add(hfrom)
        if end_ts is not None:
            last_seen[ip] = max(last_seen.get(ip, 0), int(end_ts))

    out = []
    for ip, a in agg.items():
        msgs = a["msgs"]
        fails = a["fails"]
        if failed_only and fails == 0:
            continue
        if fails < (min_fails or 1):
            continue
        fail_rate = (fails / msgs * 100.0) if msgs else 0.0
        out.append({
            "ip": ip,
            "msgs": msgs,
            "fails": fails,
            "fail_rate": fail_rate,
            "domains": len(domains_by_ip[ip]),
            "last_seen": _date_from_epoch(last_seen.get(ip)),
        })

    key = {
        "fails":     lambda x: (-x["fails"], -x["msgs"]),
        "msgs":      lambda x: (-x["msgs"], -x["fails"]),
        "fail_rate": lambda x: (-x["fail_rate"], -x["fails"]),
    }.get(sort, lambda x: (-x["fails"], -x["msgs"]))
    out.sort(key=key)

    title = f"IPs (top {limit})" if limit else "IPs"
    table = Table(title=title)
    table.add_column("IP")
    table.add_column("Msgs", justify="right")
    table.add_column("Fails", justify="right")
    table.add_column("Fail%", justify="right")
    table.add_column("Domains", justify="right")
    table.add_column("Last Seen (UTC)", justify="left")

    for r in out[:limit] if limit else out:
        table.add_row(
            r["ip"],
            str(r["msgs"]),
            str(r["fails"]),
            f"{r['fail_rate']:.1f}%",
            str(r["domains"]),
            r["last_seen"] or "-",
        )

    Console().print(table)
