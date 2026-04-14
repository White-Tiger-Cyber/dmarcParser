from rich.table import Table
from rich.console import Console
import sqlite3, datetime
from collections import defaultdict

def _open(db_path):
    return sqlite3.connect(db_path)

def _date_from_epoch(ts):
    try:
        return datetime.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return None

def pct_timeline_data(db_path, days=30):
    """
    Return daily rollup as a list of dicts:
    [{date, msgs, fails, fail_rate, pct_avg, pct_min, pct_max}, ...]
    """
    con = _open(db_path)
    cur = con.cursor()

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

    cur.execute(f"""
        SELECT rc.day AS d, SUM(rc.count) AS msgs
        FROM records rc
        {rec_where}
        GROUP BY rc.day
        ORDER BY rc.day
    """, rec_params)
    msgs_by_day = {d: (m or 0) for d, m in cur.fetchall()}

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

    out = []
    for d in sorted(set(msgs_by_day) | set(fails_by_day) | set(pct_stats)):
        m = msgs_by_day.get(d, 0)
        f = fails_by_day.get(d, 0)
        rate = (f / m * 100.0) if m else 0.0
        vals = pct_stats.get(d, [])
        out.append({
            "date": d,
            "msgs": m,
            "fails": f,
            "fail_rate": round(rate, 1),
            "pct_avg": round(sum(vals) / len(vals), 1) if vals else None,
            "pct_min": min(vals) if vals else None,
            "pct_max": max(vals) if vals else None,
        })
    return out


def pct_timeline_view(db_path, days=30):
    """
    Daily rollup: msgs, estimated fails, fail %, and observed DMARC pct
    (avg [min-max] of numeric pct values reported that day).
    """
    rows = pct_timeline_data(db_path, days=days)

    tbl = Table(title="DMARC pct timeline (daily)")
    tbl.add_column("Date")
    tbl.add_column("Msgs", justify="right")
    tbl.add_column("Fails", justify="right")
    tbl.add_column("Fail%", justify="right")
    tbl.add_column("Observed pct", justify="right")

    for row in rows:
        if row["pct_avg"] is not None:
            pct_cell = f"{row['pct_avg']:.0f} [{row['pct_min']}-{row['pct_max']}]"
        else:
            pct_cell = "n/a"
        tbl.add_row(row["date"], str(row["msgs"]), str(row["fails"]),
                    f"{row['fail_rate']:.1f}%", pct_cell)

    Console().print(tbl)

def summary_data(db_path, days=None):
    """
    Return summary statistics as a dict:
    {reports, msgs, fails, fail_rate, uniq_domains, uniq_ips, date_start, date_end}
    """
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

    c.execute(f"SELECT COUNT(*) FROM reports {where_reports}", params)
    reports = c.fetchone()[0]

    c.execute(f"""
        SELECT
          COALESCE(SUM(rec.count),0),
          COALESCE(SUM(CASE
              WHEN (rec.disposition IN ('reject','quarantine')
                 OR (LOWER(COALESCE(rec.spf_result,''))!='pass' AND LOWER(COALESCE(rec.dkim_result,''))!='pass'))
              THEN rec.count ELSE 0 END),0)
        FROM records rec
        {where_join}
    """, params)
    row = c.fetchone() or (0, 0)
    msgs, fails = row[0], row[1]
    fail_rate = round((fails / msgs * 100.0) if msgs else 0.0, 1)

    c.execute(f"SELECT MIN(end_ts), MAX(end_ts) FROM reports {where_reports}", params)
    r = c.fetchone()
    date_start = _date_from_epoch(r[0]) if r and r[0] else None
    date_end   = _date_from_epoch(r[1]) if r and r[1] else None

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

    c.execute(f"""
        SELECT COUNT(DISTINCT rec.spf_domain)
        FROM records rec {where_join}
        WHERE rec.spf_domain IS NOT NULL
    """, params)
    uniq_senders = c.fetchone()[0] or 0

    return {
        "reports": reports,
        "msgs": msgs,
        "fails": fails,
        "fail_rate": fail_rate,
        "uniq_domains": uniq_domains,
        "uniq_senders": uniq_senders,
        "uniq_ips": uniq_ips,
        "date_start": date_start,
        "date_end": date_end,
    }


def summary_view(db_path, days=None):
    """High-level summary: totals, distincts, date range, rough fail rate (all window-aware)."""
    d = summary_data(db_path, days=days)

    table = Table(title="SUMMARY")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Reports", str(d["reports"]))
    table.add_row("Messages (sum count)", str(d["msgs"]))
    table.add_row("Estimated Fails", str(d["fails"]))
    table.add_row("Estimated Fail %", f"{d['fail_rate']:.1f}%")
    table.add_row("Distinct header_from", str(d["uniq_domains"]))
    table.add_row("Distinct source IPs", str(d["uniq_ips"]))
    table.add_row("Date range", f"{d['date_start'] or '-'} to {d['date_end'] or '-'}")
    Console().print(table)


def ips_data(db_path, limit=50, days=None, failed_only=False, min_fails=0, sort="fails", auth_breakdown=False):
    """
    Return IP aggregation as a list of dicts.
    Same parameters as ips_view; returns data instead of rendering.
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
        spf_fail  = (not spf_res)  or (spf_res.lower()  != "pass")
        dkim_fail = (not dkim_res) or (dkim_res.lower() != "pass")
        if spf_fail:  a["spf_fail"]  += cnt
        if dkim_fail: a["dkim_fail"] += cnt
        if spf_fail and dkim_fail: a["both_fail"] += cnt
        if disp in ("reject", "quarantine") or (spf_fail and dkim_fail):
            a["fails"] += cnt
        if disp == "reject":       a["dmarc_reject"]      += cnt
        elif disp == "quarantine": a["dmarc_quarantine"]  += cnt
        else:                      a["dmarc_none"]        += cnt
        if hfrom: domains_by_ip[ip].add(hfrom)
        if end_ts is not None:
            last_seen[ip] = max(last_seen.get(ip, 0), int(end_ts))

    out = []
    for ip, a in agg.items():
        msgs  = a["msgs"]
        fails = a["fails"]
        if failed_only and fails == 0: continue
        if min_fails and fails < min_fails: continue
        row = {
            "ip": ip,
            "msgs": msgs,
            "fails": fails,
            "fail_rate": round((fails / msgs * 100.0) if msgs else 0.0, 1),
            "domains": len(domains_by_ip[ip]),
            "last_seen": _date_from_epoch(last_seen.get(ip)),
        }
        if auth_breakdown:
            row.update({
                "spf_fail": a["spf_fail"],
                "dkim_fail": a["dkim_fail"],
                "both_fail": a["both_fail"],
                "dmarc_reject": a["dmarc_reject"],
                "dmarc_quarantine": a["dmarc_quarantine"],
                "dmarc_none": a["dmarc_none"],
            })
        out.append(row)

    key = {
        "fails":     lambda x: (-x["fails"], -x["msgs"]),
        "msgs":      lambda x: (-x["msgs"],  -x["fails"]),
        "fail_rate": lambda x: (-x["fail_rate"], -x["fails"]),
    }.get(sort, lambda x: (-x["fails"], -x["msgs"]))
    out.sort(key=key)
    return out[:limit] if limit else out


def ips_view(db_path, limit=50, days=None, failed_only=False, min_fails=0, sort="fails", auth_breakdown=False):
    """
    Aggregate by source_ip.
    Columns: IP, msgs, fails, fail%, unique header_from domains, last_seen (UTC date).
    If auth_breakdown=True, include SPF/DKIM/DMARC breakdown columns.
    """
    out = ips_data(db_path, limit=limit, days=days, failed_only=failed_only,
                   min_fails=min_fails, sort=sort, auth_breakdown=auth_breakdown)

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


# ---------------------------------------------------------------------------
# Senders view  (groups by spf_domain)
# ---------------------------------------------------------------------------

def senders_data(db_path, limit=50, sort="fails", days=None, fail_only=False):
    """
    Aggregate by spf_domain.
    Returns [{domain, msgs, fails, fail_rate, forwarded, fwd_rate, ips, last_seen}, ...]
    Forwarded = SPF fail AND DKIM pass (classic email-forwarding signature).
    """
    conn = _open(db_path)
    c = conn.cursor()

    params = []
    join_clause = "JOIN reports r ON r.fp_report = rec.fp_report"
    where_clause = ""
    if days:
        c.execute("SELECT MAX(end_ts) FROM reports")
        max_end = c.fetchone()[0]
        if max_end:
            cutoff = int(max_end) - days * 86400
            where_clause = "WHERE r.end_ts >= ?"
            params = [cutoff]

    rows = c.execute(f"""
        SELECT
            COALESCE(rec.spf_domain, '[none]')          AS domain,
            SUM(rec.count)                              AS msgs,
            SUM(CASE
                WHEN (rec.disposition IN ('reject','quarantine')
                  OR (LOWER(COALESCE(rec.spf_result,''))  != 'pass'
                  AND LOWER(COALESCE(rec.dkim_result,'')) != 'pass'))
                THEN rec.count ELSE 0 END)              AS fails,
            SUM(CASE
                WHEN LOWER(COALESCE(rec.spf_result,''))  != 'pass'
                 AND LOWER(COALESCE(rec.dkim_result,'')) =  'pass'
                THEN rec.count ELSE 0 END)              AS forwarded,
            COUNT(DISTINCT rec.source_ip)               AS ips,
            MAX(rec.day)                                AS last_seen
        FROM records rec
        {join_clause}
        {where_clause}
        GROUP BY COALESCE(rec.spf_domain, '[none]')
    """, params).fetchall()

    out = []
    for domain, msgs, fails, forwarded, ips, last_seen in rows:
        msgs = msgs or 0
        fails = fails or 0
        forwarded = forwarded or 0
        if fail_only and fails == 0:
            continue
        out.append({
            "domain":    domain,
            "msgs":      msgs,
            "fails":     fails,
            "fail_rate": round((fails / msgs * 100.0) if msgs else 0.0, 1),
            "forwarded": forwarded,
            "fwd_rate":  round((forwarded / msgs * 100.0) if msgs else 0.0, 1),
            "ips":       ips or 0,
            "last_seen": last_seen,
        })

    sort_key = {
        "fails":     lambda x: (-x["fails"],     -x["msgs"]),
        "msgs":      lambda x: (-x["msgs"],      -x["fails"]),
        "fail_rate": lambda x: (-x["fail_rate"], -x["fails"]),
        "forwarded": lambda x: (-x["forwarded"], -x["msgs"]),
    }.get(sort, lambda x: (-x["fails"], -x["msgs"]))
    out.sort(key=sort_key)
    return out[:limit] if limit else out


def senders_view(db_path, limit=50, sort="fails", days=None, fail_only=False):
    """CLI render of senders_data."""
    rows = senders_data(db_path, limit=limit, sort=sort, days=days, fail_only=fail_only)
    title = f"SENDERS (top {limit})" if limit else "SENDERS"
    table = Table(title=title)
    table.add_column("Sending Domain")
    table.add_column("Msgs",      justify="right")
    table.add_column("Fails",     justify="right")
    table.add_column("Fail%",     justify="right")
    table.add_column("Forwarded", justify="right")
    table.add_column("Fwd%",      justify="right")
    table.add_column("IPs",       justify="right")
    table.add_column("Last Seen")
    for r in rows:
        table.add_row(
            r["domain"], str(r["msgs"]), str(r["fails"]),
            f"{r['fail_rate']:.1f}%", str(r["forwarded"]),
            f"{r['fwd_rate']:.1f}%", str(r["ips"]),
            r["last_seen"] or "-",
        )
    Console().print(table)


# ---------------------------------------------------------------------------
# Reporters view  (groups by org_name from reports table)
# ---------------------------------------------------------------------------

def reporters_data(db_path):
    """
    Aggregate by reporting organization.
    Returns [{reporter, reports, messages, first_seen, last_seen}, ...]
    """
    conn = _open(db_path)
    rows = conn.execute("""
        SELECT
            COALESCE(rp.org_name, '[unknown]')  AS reporter,
            COUNT(DISTINCT rp.fp_report)        AS report_count,
            COALESCE(SUM(rec.count), 0)         AS messages,
            MIN(rp.end_ts)                      AS first_ts,
            MAX(rp.end_ts)                      AS last_ts
        FROM reports rp
        LEFT JOIN records rec ON rec.fp_report = rp.fp_report
        GROUP BY COALESCE(rp.org_name, '[unknown]')
        ORDER BY report_count DESC
    """).fetchall()
    return [
        {
            "reporter":   r[0],
            "reports":    r[1],
            "messages":   r[2],
            "first_seen": _date_from_epoch(r[3]),
            "last_seen":  _date_from_epoch(r[4]),
        }
        for r in rows
    ]


def reporters_view(db_path):
    """CLI render of reporters_data."""
    rows = reporters_data(db_path)
    table = Table(title="REPORTERS")
    table.add_column("Reporter")
    table.add_column("Reports",    justify="right")
    table.add_column("Messages",   justify="right")
    table.add_column("First Seen")
    table.add_column("Last Seen")
    for r in rows:
        table.add_row(
            r["reporter"], str(r["reports"]), str(r["messages"]),
            r["first_seen"] or "-", r["last_seen"] or "-",
        )
    Console().print(table)


# ---------------------------------------------------------------------------
# Drill-down: Reporter detail
# ---------------------------------------------------------------------------

def reporter_detail_data(db_path, org_name):
    """
    All reports from a given org_name, each with message + fail counts.
    Returns {reporter, reports: [{fp_report, report_id, begin_ts, end_ts,
                                  policy_domain, msgs, fails}, ...]}
    """
    conn = _open(db_path)

    org_filter = None if org_name == "[unknown]" else org_name
    if org_filter:
        report_rows = conn.execute("""
            SELECT rp.fp_report, rp.report_id, rp.begin_ts, rp.end_ts,
                   rp.policy_domain
            FROM reports rp
            WHERE rp.org_name = ?
            ORDER BY rp.end_ts DESC
        """, (org_filter,)).fetchall()
    else:
        report_rows = conn.execute("""
            SELECT rp.fp_report, rp.report_id, rp.begin_ts, rp.end_ts,
                   rp.policy_domain
            FROM reports rp
            WHERE rp.org_name IS NULL
            ORDER BY rp.end_ts DESC
        """).fetchall()

    fps = [r[0] for r in report_rows]
    counts = {}
    if fps:
        placeholders = ",".join("?" * len(fps))
        agg_rows = conn.execute(f"""
            SELECT fp_report,
                   COALESCE(SUM(count), 0) AS msgs,
                   COALESCE(SUM(CASE
                       WHEN (disposition IN ('reject','quarantine')
                         OR (LOWER(COALESCE(spf_result,''))  != 'pass'
                         AND LOWER(COALESCE(dkim_result,'')) != 'pass'))
                       THEN count ELSE 0 END), 0) AS fails
            FROM records
            WHERE fp_report IN ({placeholders})
            GROUP BY fp_report
        """, fps).fetchall()
        counts = {fp: (m, f) for fp, m, f in agg_rows}

    reports = []
    for fp, report_id, begin_ts, end_ts, policy_domain in report_rows:
        msgs, fails = counts.get(fp, (0, 0))
        reports.append({
            "fp_report":     fp,
            "report_id":     report_id,
            "begin_ts":      _date_from_epoch(begin_ts),
            "end_ts":        _date_from_epoch(end_ts),
            "policy_domain": policy_domain,
            "msgs":          msgs,
            "fails":         fails,
            "fail_rate":     round((fails / msgs * 100.0) if msgs else 0.0, 1),
        })

    return {"reporter": org_name, "reports": reports}


# ---------------------------------------------------------------------------
# Drill-down: IP detail
# ---------------------------------------------------------------------------

def ip_detail_data(db_path, ip):
    """
    All records for a given source IP, grouped by report.
    Returns {ip, groups: [{report: {...}, records: [...]}, ...]}
    """
    conn = _open(db_path)

    report_rows = conn.execute("""
        SELECT rp.fp_report, rp.org_name, rp.report_id,
               rp.begin_ts, rp.end_ts, rp.policy_domain
        FROM reports rp
        WHERE rp.fp_report IN (
            SELECT DISTINCT fp_report FROM records WHERE source_ip = ?
        )
        ORDER BY rp.end_ts DESC
    """, (ip,)).fetchall()

    rec_rows = conn.execute("""
        SELECT fp_report, day, spf_domain, spf_result, dkim_result, disposition, count
        FROM records
        WHERE source_ip = ?
    """, (ip,)).fetchall()

    recs_by_report = defaultdict(list)
    for fp, day, spf_dom, spf_res, dkim_res, disp, cnt in rec_rows:
        recs_by_report[fp].append({
            "day": day, "spf_domain": spf_dom or "-",
            "spf_result": spf_res or "-", "dkim_result": dkim_res or "-",
            "disposition": disp or "-", "count": cnt or 0,
        })

    groups = []
    for fp, org, report_id, begin_ts, end_ts, policy_domain in report_rows:
        groups.append({
            "report": {
                "fp_report":     fp,
                "org_name":      org or "[unknown]",
                "report_id":     report_id,
                "begin_ts":      _date_from_epoch(begin_ts),
                "end_ts":        _date_from_epoch(end_ts),
                "policy_domain": policy_domain,
            },
            "records": recs_by_report.get(fp, []),
        })

    return {"ip": ip, "groups": groups}


# ---------------------------------------------------------------------------
# Drill-down: Report detail
# ---------------------------------------------------------------------------

def report_detail_data(db_path, fp_report):
    """
    Full detail for a single report.
    Returns {report: {...}, records: [...]}
    """
    conn = _open(db_path)

    rp = conn.execute("""
        SELECT fp_report, org_name, report_id, begin_ts, end_ts,
               policy_domain, p, sp, aspf, adkim, pct, fo, np
        FROM reports WHERE fp_report = ?
    """, (fp_report,)).fetchone()

    if not rp:
        return None

    recs = conn.execute("""
        SELECT source_ip, count, envelope_from, header_from,
               spf_result, spf_domain, dkim_result, disposition
        FROM records WHERE fp_report = ?
        ORDER BY count DESC
    """, (fp_report,)).fetchall()

    return {
        "report": {
            "fp_report":     rp[0],
            "org_name":      rp[1] or "[unknown]",
            "report_id":     rp[2],
            "begin_ts":      _date_from_epoch(rp[3]),
            "end_ts":        _date_from_epoch(rp[4]),
            "policy_domain": rp[5],
            "p":   rp[6],  "sp":   rp[7],
            "aspf": rp[8], "adkim": rp[9],
            "pct": rp[10], "fo":   rp[11], "np": rp[12],
        },
        "records": [
            {
                "source_ip":     r[0],
                "count":         r[1] or 0,
                "envelope_from": r[2] or "-",
                "header_from":   r[3] or "-",
                "spf_result":    r[4] or "-",
                "spf_domain":    r[5] or "-",
                "dkim_result":   r[6] or "-",
                "disposition":   r[7] or "-",
            }
            for r in recs
        ],
    }


# ---------------------------------------------------------------------------
# Drill-down: Sender detail
# ---------------------------------------------------------------------------

def sender_detail_data(db_path, domain):
    """
    All records for a given spf_domain, grouped by report.
    Returns {domain, groups: [{report: {...}, records: [...]}, ...]}
    """
    conn = _open(db_path)

    spf_filter = None if domain == "[none]" else domain
    if spf_filter:
        report_rows = conn.execute("""
            SELECT rp.fp_report, rp.org_name, rp.report_id,
                   rp.begin_ts, rp.end_ts, rp.policy_domain
            FROM reports rp
            WHERE rp.fp_report IN (
                SELECT DISTINCT fp_report FROM records WHERE spf_domain = ?
            )
            ORDER BY rp.end_ts DESC
        """, (spf_filter,)).fetchall()
        rec_rows = conn.execute("""
            SELECT fp_report, day, source_ip, spf_result, dkim_result, disposition, count
            FROM records WHERE spf_domain = ?
        """, (spf_filter,)).fetchall()
    else:
        report_rows = conn.execute("""
            SELECT rp.fp_report, rp.org_name, rp.report_id,
                   rp.begin_ts, rp.end_ts, rp.policy_domain
            FROM reports rp
            WHERE rp.fp_report IN (
                SELECT DISTINCT fp_report FROM records WHERE spf_domain IS NULL
            )
            ORDER BY rp.end_ts DESC
        """).fetchall()
        rec_rows = conn.execute("""
            SELECT fp_report, day, source_ip, spf_result, dkim_result, disposition, count
            FROM records WHERE spf_domain IS NULL
        """).fetchall()

    recs_by_report = defaultdict(list)
    for fp, day, source_ip, spf_res, dkim_res, disp, cnt in rec_rows:
        recs_by_report[fp].append({
            "day": day, "source_ip": source_ip or "-",
            "spf_result": spf_res or "-", "dkim_result": dkim_res or "-",
            "disposition": disp or "-", "count": cnt or 0,
        })

    groups = []
    for fp, org, report_id, begin_ts, end_ts, policy_domain in report_rows:
        groups.append({
            "report": {
                "fp_report":     fp,
                "org_name":      org or "[unknown]",
                "report_id":     report_id,
                "begin_ts":      _date_from_epoch(begin_ts),
                "end_ts":        _date_from_epoch(end_ts),
                "policy_domain": policy_domain,
            },
            "records": recs_by_report.get(fp, []),
        })

    return {"domain": domain, "groups": groups}
