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

def pct_timeline_view(db_path, days=30):
    """
    Daily rollup: msgs, estimated fails, fail %, and observed DMARC pct
    (avg [min–max] of numeric pct values reported that day).
    """
    con = _open(db_path)
    cur = con.cursor()

    # Build WHERE + params (avoid '? inside quotes')
    if days:
        rec_where = "WHERE rc.day >= date('now', ?)"
        rec_params = (f"-{days} day",)
        rep_where = "AND date(rp.end_ts,'unixepoch') >= date('now', ?)"
        rep_params = (f"-{days} day",)
    else:
        rec_where = ""
        rec_params = ()
        rep_where = ""
        rep_params = ()

    # 1) Messages per day
    cur.execute(f"""
        SELECT rc.day AS d, SUM(rc.count) AS msgs
        FROM records rc
        {rec_where}
        GROUP BY rc.day
        ORDER BY rc.day
    """, rec_params)
    msgs_by_day = {d: (m or 0) for d, m in cur.fetchall()}

    # 2) Estimated fails per day
    cur.execute(f"""
        SELECT rc.day AS d, SUM(rc.count) AS fails
        FROM records rc
        {rec_where} {"AND" if rec_where else "WHERE"} (
            COALESCE(rc.disposition,'') IN ('reject','quarantine')
            OR (LOWER(COALESCE(rc.spf_result,''))!='pass' AND LOWER(COALESCE(rc.dkim_result,''))!='pass')
        )
        GROUP BY rc.day
        ORDER BY rc.day
    """, rec_params)
    fails_by_day = {d: (f or 0) for d, f in cur.fetchall()}

    # 3) Observed pct stats per day from reports (avg [min–max])
    cur.execute(f"""
        SELECT date(rp.end_ts,'unixepoch') AS d,
               CAST(rp.pct AS INTEGER) AS pct_val
        FROM reports rp
        WHERE rp.pct GLOB '[0-9]*'
        {rep_where}
        ORDER BY d, pct_val
    """, rep_params)
    pct_stats = {}
    for d, val in cur.fetchall():
        pct_stats.setdefault(d, []).append(val)

    # Render
    tbl = Table(title="DMARC pct timeline (daily)")
    tbl.add_column("Date")
    tbl.add_column("Msgs", justify="right")
    tbl.add_column("Fails", justify="right")
    tbl.add_column("Fail%", justify="right")
    tbl.add_column("Observed pct", justify="right")  # avg [min–max]

    all_days = sorted(set(msgs_by_day) | set(fails_by_day) | set(pct_stats))
    for d in all_days:
        m = msgs_by_day.get(d, 0)
        f = fails_by_day.get(d, 0)
        rate = (f / m * 100.0) if m else 0.0
        if d in pct_stats and pct_stats[d]:
            vals = pct_stats[d]
            avg = sum(vals) / len(vals)
            pct_cell = f"{avg:.0f} [{min(vals)}–{max(vals)}]"
        else:
            pct_cell = "—"
        tbl.add_row(d, str(m), str(f), f"{rate:.1f}%", pct_cell)

    Console().print(tbl)

def summary_view(db_path, days=None):
    """High-level summary: totals, distincts, date range, rough fail rate (all window-aware)."""
    conn = _open(db_path)
    c = conn.cursor()

    where_reports = ""
    where_join = ""
    params = []
    if days:
        c.execute("SELECT MAX(end_ts) FROM reports")
        max_end = c.fetchone()[0]
        if max_end:
            cutoff = int(max_end) - days * 86400
            where_reports = "WHERE end_ts >= ?"
            where_join = "JOIN reports r ON r.fp_report = rec.fp_report AND r.end_ts >= ?"
            params = [cutoff]

    # totals (reports count)
    c.execute(f"SELECT COUNT(*) FROM reports {where_reports}", params)
    reports = c.fetchone()[0]

    # msgs & fails within window via join
    c.execute(f"""
        SELECT
          COALESCE(SUM(rec.count),0) AS msgs,
          COALESCE(SUM(CASE
              WHEN (rec.disposition IN ('reject','quarantine')
                 OR (LOWER(COALESCE(rec.spf_result,''))!='pass' AND LOWER(COALESCE(rec.dkim_result,''))!='pass'))
              THEN rec.count ELSE 0 END),0) AS fails
        FROM records rec
        {where_join}
    """, params)
    row = c.fetchone() or (0,0)
    msgs, fails = row[0], row[1]
    fail_rate = (fails / msgs * 100.0) if msgs else 0.0

    # date range from reports
    c.execute(f"SELECT MIN(end_ts), MAX(end_ts) FROM reports {where_reports}", params)
    r = c.fetchone()
    start = _date_from_epoch(r[0]) if r and r[0] else None
    end   = _date_from_epoch(r[1]) if r and r[1] else None

    # distincts (window-aware)
    c.execute(f"""
        SELECT COUNT(DISTINCT rec.header_from)
        FROM records rec {where_join}
        WHERE rec.header_from IS NOT NULL
    """, params)
    uniq_domains = c.fetchone()[0] or 0

    c.execute(f"""
        SELECT COUNT(DISTINCT rec.source_ip)
        FROM records rec {where_join}
    """, params)
    uniq_ips = c.fetchone()[0] or 0

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

    # Render rows
    for rd in rows_out[:limit] if limit else rows_out:
        table.add_row(
            rd["domain"],
            str(rd["msgs"]),
            str(rd["fails"]),
            f"{rd['fail_rate']:.1f}%",
            f"{rd['aligned_rate']:.1f}%",
            str(rd["ips"]),
        )

    Console().print(table)

def ips_view(db_path, limit=50, days=None, failed_only=False, min_fails=1, sort="fails", auth_breakdown=False):
    """
    Aggregate by source_ip.
    Columns: IP, msgs, fails, fail%, unique header_from domains, last_seen (UTC date).
    If auth_breakdown=True, include SPF/DKIM/DMARC breakdown columns.
    """
    conn = _open(db_path)
    c = conn.cursor()

    params = []
    where = ""
    if days:
        c.execute("SELECT MAX(end_ts) FROM reports")
        max_end = c.fetchone()[0]
        if max_end:
            cutoff = int(max_end) - days * 86400
            where = "WHERE r.end_ts >= ?"
            params = [cutoff]

    rows = c.execute(f"""
        SELECT rec.source_ip, rec.count, rec.disposition,
               rec.spf_result, rec.dkim_result, rec.header_from, r.end_ts
        FROM records rec
        JOIN reports r ON r.fp_report = rec.fp_report
        {where}
    """, params).fetchall()

    agg = {}
    domains_by_ip = defaultdict(set)
    last_seen = {}

    for ip, cnt, disp, spf_res, dkim_res, hfrom, end_ts in rows:
        if not ip:
            continue
        a = agg.setdefault(ip, {
            "msgs": 0, "fails": 0,
            "spf_fail": 0, "dkim_fail": 0, "both_fail": 0,
            "dmarc_reject": 0, "dmarc_quarantine": 0, "dmarc_none": 0
        })
        cnt = cnt or 0
        a["msgs"] += cnt

        spf_fail = (not spf_res) or (spf_res.lower() != "pass")
        dkim_fail = (not dkim_res) or (dkim_res.lower() != "pass")
        if spf_fail:
            a["spf_fail"] += cnt
        if dkim_fail:
            a["dkim_fail"] += cnt
        if spf_fail and dkim_fail:
            a["both_fail"] += cnt

        # failure as in "DMARC would fail"
        if disp in ("reject", "quarantine") or (spf_fail and dkim_fail):
            a["fails"] += cnt

        if disp == "reject":
            a["dmarc_reject"] += cnt
        elif disp == "quarantine":
            a["dmarc_quarantine"] += cnt
        else:
            a["dmarc_none"] += cnt

        if hfrom:
            domains_by_ip[ip].add(hfrom)
        if end_ts is not None:
            last_seen[ip] = max(last_seen.get(ip, 0), int(end_ts))

    # build output rows
    out = []
    for ip, a in agg.items():
        msgs = a["msgs"]
        fails = a["fails"]
        if failed_only and fails == 0:
            continue
        if fails < (min_fails or 1):
            continue
        fail_rate = (fails / msgs * 100.0) if msgs else 0.0

        row = {
            "ip": ip,
            "msgs": msgs,
            "fails": fails,
            "fail_rate": fail_rate,
            "domains": len(domains_by_ip[ip]),
            "last_seen": _date_from_epoch(last_seen.get(ip)),
        }

        if auth_breakdown:
            # retain fail counters for later pass computation
            row.update({
                "spf_fail": a["spf_fail"],
                "dkim_fail": a["dkim_fail"],
                "both_fail": a["both_fail"],
                "dmarc_reject": a["dmarc_reject"],
                "dmarc_quarantine": a["dmarc_quarantine"],
                "dmarc_none": a["dmarc_none"],
            })

        out.append(row)

    # sorting
    key = {
        "fails":     lambda x: (-x["fails"], -x["msgs"]),
        "msgs":      lambda x: (-x["msgs"], -x["fails"]),
        "fail_rate": lambda x: (-x["fail_rate"], -x["fails"]),
    }.get(sort, lambda x: (-x["fails"], -x["msgs"]))
    out.sort(key=key)

    # table header
    title = f"IPs (top {limit})" if limit else "IPs"
    table = Table(title=title)
    table.add_column("IP")
    table.add_column("Msgs", justify="right")
    table.add_column("Fails", justify="right")
    table.add_column("Fail%", justify="right")
    if auth_breakdown:
        table.add_column("SPF=fail", justify="right")
        table.add_column("DKIM=fail", justify="right")
        table.add_column("Both=fail", justify="right")
        table.add_column("DMARC reject", justify="right")
        table.add_column("DMARC quarantine", justify="right")
        table.add_column("DMARC none", justify="right")
    table.add_column("Domains", justify="right")
    table.add_column("Last Seen (UTC)")

    if not out:
        Console().print(table)
        return

    # render rows
    for rowdata in (out[:limit] if limit else out):
        row = [
            rowdata["ip"],
            str(rowdata["msgs"]),
            str(rowdata["fails"]),
            f"{rowdata['fail_rate']:.1f}%"
        ]
        if auth_breakdown:
            # SHOW FAIL COUNTS (not passes)
            row += [
                str(rowdata["spf_fail"]),
                str(rowdata["dkim_fail"]),
                str(rowdata["both_fail"]),
                str(rowdata["dmarc_reject"]),
                str(rowdata["dmarc_quarantine"]),
                str(rowdata["dmarc_none"]),
            ]
        row += [str(rowdata["domains"]), rowdata["last_seen"] or "-"]
        table.add_row(*row)

    Console().print(table)
