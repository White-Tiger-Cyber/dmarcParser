import os, sqlite3, datetime, json, re, time

DDL = """
CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_name   TEXT NOT NULL UNIQUE,
  client_domain TEXT NOT NULL UNIQUE,
  created_at    TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ','now'))
);
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
  -- file_path is the unique key we use for “what we processed”.
  -- For files inside ZIPs we use the virtual key "zip_path::inner_name"
  file_path TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  sha256_xml TEXT,
  status TEXT NOT NULL,
  last_ingested_at TEXT,
  -- Stage 2 filename-derived metadata (nullable if unavailable)
  origin_name TEXT,
  name_provider TEXT,
  name_domain TEXT,
  name_begin_ts INTEGER,
  name_end_ts INTEGER
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

# ---------- Stage 2 additions ----------
def upsert_seen_ext(conn, *, file_path, size, mtime, sha256_xml, status,
                    origin_name=None, name_provider=None, name_domain=None,
                    name_begin_ts=None, name_end_ts=None):
    """
    Same as upsert_seen, with filename-derived metadata persisted for later analytics.
    """
    now = datetime.datetime.utcnow().isoformat()+"Z"
    conn.execute("""
        INSERT INTO files_seen(file_path,size,mtime,sha256_xml,status,last_ingested_at,
                               origin_name,name_provider,name_domain,name_begin_ts,name_end_ts)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(file_path) DO UPDATE SET
            size=excluded.size,
            mtime=excluded.mtime,
            sha256_xml=excluded.sha256_xml,
            status=excluded.status,
            last_ingested_at=excluded.last_ingested_at,
            origin_name=COALESCE(excluded.origin_name, files_seen.origin_name),
            name_provider=COALESCE(excluded.name_provider, files_seen.name_provider),
            name_domain=COALESCE(excluded.name_domain, files_seen.name_domain),
            name_begin_ts=COALESCE(excluded.name_begin_ts, files_seen.name_begin_ts),
            name_end_ts=COALESCE(excluded.name_end_ts, files_seen.name_end_ts)
    """, (file_path,size,mtime,sha256_xml,status,now,
          origin_name,name_provider,name_domain,name_begin_ts,name_end_ts))
    conn.commit()

def providers_summary_for_client(conn, client_key):
    """
    Return list of dicts: [{'provider': 'yahoo.com', 'count': 42, 'latest_end_ts': 1752796799}, ...]
    Only includes rows whose filename-domain matches the client's registered domain and status='parsed'.
    """
    cur = conn.execute("SELECT client_domain FROM clients WHERE client_name=?", (client_key,))
    row = cur.fetchone()
    if not row: return []
    client_domain = row[0]
    q = """
        SELECT name_provider, COUNT(*) as cnt, MAX(name_end_ts) as latest_end
        FROM files_seen
        WHERE status='parsed' AND name_domain = ?
        GROUP BY name_provider
        ORDER BY cnt DESC, name_provider ASC
    """
    out = []
    for prov, cnt, latest in conn.execute(q, (client_domain,)):
        out.append({"provider": prov, "count": cnt or 0, "latest_end_ts": latest})
    return out

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

# ---------------------------
# Stage 1: Client management
# ---------------------------
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)(?!.*--)[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+$"
)

def _validate_domain(domain: str) -> str:
    """
    Very small domain validator; returns the normalized (lowercased) domain.
    Raises ValueError on bad input.
    """
    d = (domain or "").strip().lower().rstrip(".")
    if not _DOMAIN_RE.match(d) or "." not in d:
        raise ValueError(f"Invalid domain name: {domain!r}")
    return d

def create_client(conn, client_name: str, client_domain: str) -> int:
    """
    Create a client row. This is intended to be called by the CLI.
    Enforces uniqueness of both client_name and client_domain.
    """
    client_name = (client_name or "").strip()
    if not client_name:
        raise ValueError("client_name is required")
    client_domain = _validate_domain(client_domain)
    with conn:
        cur = conn.execute(
            "INSERT INTO clients (client_name, client_domain) VALUES (?, ?)",
            (client_name, client_domain),
        )
        return cur.lastrowid

def get_client_by_name(conn, client_name: str):
    """
    Return (id, client_name, client_domain, created_at) or None.
    """
    cur = conn.execute(
        "SELECT id, client_name, client_domain, created_at FROM clients WHERE client_name = ?",
        ((client_name or "").strip(),),
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Shared index DB  (~/.dmarcParser/index.db)
# Tracks all clients centrally + email processing audit log.
# ---------------------------------------------------------------------------

_INDEX_DDL = """
CREATE TABLE IF NOT EXISTS clients (
    client_name TEXT PRIMARY KEY,
    domain      TEXT NOT NULL UNIQUE,
    db_path     TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_emails (
    message_id   TEXT PRIMARY KEY,
    processed_at INTEGER NOT NULL,
    client_name  TEXT,
    status       TEXT NOT NULL,
    notes        TEXT
);
"""

_INDEX_DB_PATH = os.path.expanduser("~/.dmarcParser/index.db")


def open_index_db(path=None):
    """Open (or create) the shared index DB.  Returns a sqlite3 connection."""
    path = path or _INDEX_DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_INDEX_DDL)
    conn.commit()
    return conn


def ensure_client_for_domain(index_db_path, policy_domain: str):
    """
    Find or auto-create a client for `policy_domain`.

    Returns a dict:
        {client_name, db_path, is_new}

    If the domain is not yet registered:
    1.  client_name  = policy_domain  (e.g. "whitetigercyber.com")
    2.  db_path      = ~/.dmarcParser/clients/<domain>.db
    3.  Creates the per-client SQLite DB and registers the client inside it
    4.  Registers the mapping in index.db
    """
    domain = _validate_domain(policy_domain)
    idx = open_index_db(index_db_path)

    row = idx.execute(
        "SELECT client_name, db_path FROM clients WHERE domain=?", (domain,)
    ).fetchone()

    if row:
        idx.close()
        return {"client_name": row[0], "db_path": row[1], "is_new": False}

    # Auto-create
    client_name = domain
    clients_dir = os.path.expanduser("~/.dmarcParser/clients")
    db_path = os.path.join(clients_dir, f"{client_name}.db")

    # Create per-client DB and register client row inside it
    client_conn = open_db(db_path)
    try:
        create_client(client_conn, client_name, domain)
    except Exception:
        pass  # May already exist (e.g., race or retry)
    client_conn.close()

    # Register in index
    idx.execute(
        "INSERT OR IGNORE INTO clients(client_name, domain, db_path, created_at) VALUES (?,?,?,?)",
        (client_name, domain, db_path, int(time.time())),
    )
    idx.commit()
    idx.close()

    return {"client_name": client_name, "db_path": db_path, "is_new": True}


def list_all_clients_from_index(index_db_path=None):
    """
    Return list of dicts: [{client_name, domain, db_path, created_at}, ...]
    from the shared index DB.
    """
    idx = open_index_db(index_db_path or _INDEX_DB_PATH)
    rows = idx.execute(
        "SELECT client_name, domain, db_path, created_at FROM clients ORDER BY client_name"
    ).fetchall()
    idx.close()
    return [
        {"client_name": r[0], "domain": r[1], "db_path": r[2], "created_at": r[3]}
        for r in rows
    ]
