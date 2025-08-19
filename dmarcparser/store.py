
import os, sqlite3, datetime, json

DDL = """
CREATE TABLE IF NOT EXISTS ingest_sessions (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  root_path TEXT NOT NULL,
  client_key TEXT NOT NULL,
  cli_args_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingest_decisions (
  session_id INTEGER NOT NULL,
  file_path TEXT NOT NULL,
  decision TEXT NOT NULL,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS files_seen (
  file_path TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  sha256_xml TEXT,
  status TEXT NOT NULL,
  last_ingested_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_seen_sha ON files_seen(sha256_xml);

CREATE TABLE IF NOT EXISTS reports (
  fp_report TEXT PRIMARY KEY,
  org_name TEXT,
  report_id TEXT,
  begin_ts INTEGER,
  end_ts INTEGER,
  policy_domain TEXT,
  p TEXT, sp TEXT, aspf TEXT, adkim TEXT, pct TEXT, fo TEXT, np TEXT,
  sha256_xml TEXT NOT NULL,
  ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_domain ON reports(policy_domain);
CREATE INDEX IF NOT EXISTS idx_reports_time ON reports(end_ts);

CREATE TABLE IF NOT EXISTS records (
  fp_report TEXT NOT NULL,
  source_ip TEXT NOT NULL,
  count INTEGER NOT NULL,
  envelope_from TEXT,
  header_from TEXT,
  spf_result TEXT,
  spf_domain TEXT,
  dkim_result TEXT,
  dkim_results_json TEXT,
  disposition TEXT,
  day TEXT,
  FOREIGN KEY(fp_report) REFERENCES reports(fp_report)
);
CREATE INDEX IF NOT EXISTS idx_records_domain ON records(header_from);
CREATE INDEX IF NOT EXISTS idx_records_ip ON records(source_ip);
CREATE INDEX IF NOT EXISTS idx_records_day ON records(day);
"""

def open_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    conn.commit()
    return conn

def start_session(conn, root_path, client_key, cli_args_json):
    cur = conn.cursor()
    cur.execute("INSERT INTO ingest_sessions(started_at, root_path, client_key, cli_args_json) VALUES (?,?,?,?)",
                (datetime.datetime.utcnow().isoformat()+"Z", root_path, client_key, cli_args_json))
    conn.commit()
    return cur.lastrowid

def log_decision(conn, session_id, file_path, decision, detail=None):
    conn.execute("INSERT INTO ingest_decisions(session_id, file_path, decision, detail) VALUES (?,?,?,?)",
                 (session_id, file_path, decision, detail))
    conn.commit()

def get_seen(conn, file_path):
    cur = conn.execute("SELECT file_path,size,mtime,sha256_xml,status FROM files_seen WHERE file_path=?", (file_path,))
    row = cur.fetchone()
    if not row: return None
    return {"file_path":row[0], "size":row[1], "mtime":row[2], "sha256_xml":row[3], "status":row[4]}

def upsert_seen(conn, file_path, size, mtime, sha256_xml, status):
    now = datetime.datetime.utcnow().isoformat()+"Z"
    conn.execute("""INSERT INTO files_seen(file_path,size,mtime,sha256_xml,status,last_ingested_at)
                    VALUES (?,?,?,?,?,?)
                    ON CONFLICT(file_path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime,
                    sha256_xml=excluded.sha256_xml, status=excluded.status, last_ingested_at=excluded.last_ingested_at
                 """, (file_path,size,mtime,sha256_xml,status,now))
    conn.commit()

def ingest_report_summary(conn, session_id):
    cur = conn.execute("SELECT decision, COUNT(*) FROM ingest_decisions WHERE session_id=? GROUP BY decision", (session_id,))
    return dict(cur.fetchall())

def upsert_report(conn, fp_report, meta, policy, sha):
    now = datetime.datetime.utcnow().isoformat()+"Z"
    conn.execute("""INSERT OR IGNORE INTO reports
        (fp_report, org_name, report_id, begin_ts, end_ts, policy_domain, p, sp, aspf, adkim, pct, fo, np, sha256_xml, ingested_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (meta.get("fp") or fp_report, meta.get("org_name"), meta.get("report_id"), meta.get("begin"), meta.get("end"),
         policy.get("domain"), policy.get("p"), policy.get("sp"), policy.get("aspf"), policy.get("adkim"),
         policy.get("pct"), policy.get("fo"), policy.get("np"), sha, now))
    conn.commit()

def insert_records(conn, fp_report, end_ts, recs):
    try:
        day = datetime.datetime.utcfromtimestamp(int(end_ts)).strftime("%Y-%m-%d")
    except Exception:
        day = None
    import json
    for r in recs:
        conn.execute("""INSERT INTO records(fp_report, source_ip, count, envelope_from, header_from,
                         spf_result, spf_domain, dkim_result, dkim_results_json, disposition, day)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (fp_report, r.get("source_ip"), r.get("count",1), r.get("envelope_from"),
                      r.get("header_from"), r.get("spf_result"), r.get("spf_domain"),
                      r.get("dkim_result"), json.dumps(r.get("dkim_results") or []),
                      r.get("disposition"), day))
    conn.commit()
