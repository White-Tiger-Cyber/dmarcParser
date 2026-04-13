import os, io, gzip, zipfile, hashlib, re, json, tempfile, shutil
from . import store
from .xmlparse import parse_rua_xml

XML_DECL_RE = re.compile(br'\s*<\?xml', re.I)

# accept ...xml, ...xml.gz, and ...__<n>.xml / ...__<n>.xml.gz (common from some providers)
# Accept: <provider>!<domain>!<epoch_begin>!<epoch_end>[anything].xml[.gz]
FNAME_RE = re.compile(
    r'^([^!]+)!([^!]+)!([0-9]{9,14})!([0-9]{9,14}).*\.xml(?:\.gz)?$',
    re.I
)

def discover_files(root):
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            lower = name.lower()
            if lower.endswith((".xml", ".gz", ".zip")):
                yield os.path.join(dirpath, name)

# ----------------------------
# Robust filename metadata parse
# ----------------------------
def parse_filename_meta(origin_name: str):
    """
    Accept very loose variants such as:
      provider!domain!<token3>!<token4>[anything].xml[.gz]
    - provider, domain: returned lowercased and stripped
    - begin/end: only set if token looks like a unix epoch (9–14 digits).
                 Otherwise None (we'll fallback to XML contents later).
    """
    name = origin_name
    # strip any trailing .gz (we already normalized earlier but safe to repeat)
    if name.lower().endswith(".gz"):
        name = name[:-3]
    # take only the basename
    name = os.path.basename(name)
    # split on '!' and grab the first 4 tokens if available
    parts = name.split('!')
    if len(parts) < 2:
        return None

    provider = parts[0].strip().lower()
    domain   = parts[1].strip().lower()

    def token_to_epoch(tok: str):
        """
        Extract a plausible unix epoch from a token.
        Handles variants like:
          1731974400           -> 1731974400
          1731974400__1        -> 1731974400   (mail.ru duplicates)
          1731974400.xml       -> 1731974400
          1731974400__1.xml    -> 1731974400
        """
        base = tok
        # drop extension and anything that follows first dot
        base = base.split('.', 1)[0]
        # strip trailing "__<n>" duplicate marker (mail.ru style)
        base = re.sub(r'__\d+$', '', base)
        # find a 9–14 digit run (seconds or ms); take the first occurrence
        m = re.search(r'(\d{9,14})', base)
        if not m:
            return None
        digits = m.group(1)
        try:
            return int(digits)
        except Exception:
            return None

    begin_ts = token_to_epoch(parts[2]) if len(parts) > 2 else None
    end_ts   = token_to_epoch(parts[3]) if len(parts) > 3 else None

    return {
        "provider": provider or None,
        "domain":   domain   or None,
        "begin_ts": begin_ts,
        "end_ts":   end_ts,
    }

def providers_summary_all(conn):
    """
    Return provider counts + latest_end across ALL rows (no domain filter).
    Includes rows with NULL name_domain/name_provider.
    """
    q = """
      SELECT name_provider, COUNT(*) AS cnt, MAX(name_end_ts) AS latest_end
      FROM files_seen
      WHERE status='parsed'
      GROUP BY name_provider
      ORDER BY cnt DESC, name_provider ASC
    """
    return [
      {"provider": prov, "count": cnt or 0, "latest_end_ts": latest}
      for (prov, cnt, latest) in conn.execute(q)
    ]

def get_client_domain(conn, client_key: str):
    cur = conn.execute("SELECT client_domain FROM clients WHERE client_name=?", (client_key,))
    row = cur.fetchone()
    return row[0] if row else None

def read_file_bytes(path):
    with open(path, "rb") as f:
        return f.read()

def is_xml_bytes(b):
    if not b: return False
    if XML_DECL_RE.match(b[:100] or b""): return True
    return b"<feedback" in (b[:4096] + b[-4096:])

def gunzip_bytes(b):
    try: return gzip.decompress(b)
    except Exception:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(b)) as g: return g.read()
        except Exception: return None

def canonical_xml_hash(xml_bytes):
    norm = xml_bytes.strip().replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    return hashlib.sha256(norm).hexdigest()

def handle_zip(path):
    xml_candidates = []
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                name = info.filename
                lower = name.lower()
                try: data = z.read(info)
                except Exception: continue
                if lower.endswith(".xml"):
                    if is_xml_bytes(data):
                        xml_candidates.append((f"{path}::{name}", data))
                elif lower.endswith(".gz"):
                    inflated = gunzip_bytes(data)
                    if inflated and is_xml_bytes(inflated):
                        xml_candidates.append((f"{path}::{name}", inflated))
            return xml_candidates
    except Exception:
        return []

def classify_and_extract(path):
    lower = path.lower()
    if lower.endswith(".xml"):
        b = read_file_bytes(path)
        if is_xml_bytes(b): return [("xml", path, b)]
        else: return [("ignored:not_xml", path, None)]
    elif lower.endswith(".gz"):
        b = read_file_bytes(path); inflated = gunzip_bytes(b)
        if inflated and is_xml_bytes(inflated): return [("xml_from_gz", path, inflated)]
        else: return [("ignored:gz_not_xml", path, None)]
    elif lower.endswith(".zip"):
        candidates = handle_zip(path)
        if candidates: return [("xml_from_zip", name, data) for (name, data) in candidates]
        else: return [("ignored:no_xml_in_archive", path, None)]
    return [("ignored:unknown", path, None)]

def ingest(root_path, client_db_path, client_key, rescan=False, dry_run=False, log=None):
    conn = store.open_db(client_db_path)
    # Look up client's registered domain (Stage 1)
    cur = conn.execute("SELECT client_domain FROM clients WHERE client_name=?", (client_key,))
    row = cur.fetchone()
    client_domain = row[0].lower() if row else None
    session_id = store.start_session(conn, root_path, client_key, json.dumps({"rescan":rescan,"dry_run":dry_run}))

    for fpath in ( [root_path] if os.path.isfile(root_path) else discover_files(root_path) ):
        try: st = os.stat(fpath)
        except FileNotFoundError: continue
        seen = store.get_seen(conn, fpath)
        if (not rescan) and seen \
           and seen["size"] == st.st_size \
           and abs(seen["mtime"] - st.st_mtime) < 0.0001 \
           and (seen.get("status") or seen["status"]) != "would_parse":
            store.log_decision(conn, session_id, fpath, "dup_file", None)
            continue

        triples = classify_and_extract(fpath)
        had_xml = False
        for kind, name, data in triples:
            if kind.startswith("ignored:"):
                store.log_decision(conn, session_id, name, kind, None); continue
            had_xml = True
            # ---- derive filename-derived metadata first (used by SHA-dup branch, dry-run, etc.) ----
            # 'name' may be "archive.zip::inner.xml" or a plain path; take just the XML basename.
            origin_name = os.path.basename(name.split("::", 1)[-1])
            # normalize for regex: strip a trailing '.gz' if present
            if origin_name.lower().endswith(".gz"):
                origin_name = origin_name[:-3]
            meta_from_name = parse_filename_meta(origin_name)
            # initialize filename-derived timestamps (may be None).
            # final_* are the values we will persist; after successful XML parse
            # we will fallback to meta['begin']/meta['end'] if these are still None.
            fname_begin = (meta_from_name or {}).get("begin_ts")
            fname_end   = (meta_from_name or {}).get("end_ts")
            final_begin, final_end = fname_begin, fname_end

            # ---- compute SHA and handle SHA-duplicate case (rescan backfills metadata) ----
            sha = canonical_xml_hash(data)
            # If filename didn't provide epochs, use the XML meta range
            if final_begin is None:
                final_begin = meta.get("begin")
            if final_end is None:
                final_end = meta.get("end")

            cur = conn.execute("SELECT 1 FROM files_seen WHERE sha256_xml=?", (sha,))
            if cur.fetchone():
                if rescan:
                    # Backfill filename metadata on rescan even if SHA already processed
                    store.upsert_seen_ext(
                        conn,
                        file_path=name, size=st.st_size, mtime=st.st_mtime,
                        sha256_xml=sha, status="parsed",
                        origin_name=origin_name,
                        name_provider=(meta_from_name or {}).get("provider"),
                        name_domain=(meta_from_name or {}).get("domain"),
                        name_begin_ts=(meta_from_name or {}).get("begin_ts"),
                        name_end_ts=(meta_from_name or {}).get("end_ts"),
                    )
                store.log_decision(conn, session_id, name, "dup_xml", None)
                continue

            # ---- client-domain filter (filename-based) ----
            if meta_from_name and client_domain:
                parsed_domain = meta_from_name["domain"]
                if parsed_domain != client_domain:
                    # Skip XMLs not for this client (by filename domain)
                    store.upsert_seen_ext(
                        conn,
                        file_path=name, size=st.st_size, mtime=st.st_mtime,
                        sha256_xml=sha, status="foreign_domain",
                        origin_name=origin_name,
                        name_provider=meta_from_name["provider"],
                        name_domain=meta_from_name["domain"],
                        name_begin_ts=meta_from_name["begin_ts"],
                        name_end_ts=meta_from_name["end_ts"],
                    )
                    store.log_decision(conn, session_id, name, "ignored:foreign_domain", parsed_domain)
                    continue

            if dry_run:
                store.log_decision(conn, session_id, name, "would_parse", None)
                store.upsert_seen_ext(
                    conn,
                    file_path=name,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    sha256_xml=sha,
                    status="would_parse",
                    origin_name=origin_name,
                    name_provider=(meta_from_name or {}).get("provider"),
                    name_domain=(meta_from_name or {}).get("domain"),
                    name_begin_ts=(meta_from_name or {}).get("begin_ts"),
                    name_end_ts=(meta_from_name or {}).get("end_ts"),
                )
                continue

            try:
                parsed = parse_rua_xml(data)
            except Exception as e:
                store.log_decision(conn, session_id, name, "error_parse", str(e))
                store.upsert_seen_ext(conn,
                    file_path=name, size=st.st_size, mtime=st.st_mtime, sha256_xml=sha, status="error_parse",
                    origin_name=origin_name,
                    name_provider=(meta_from_name or {}).get("provider"),
                    name_domain=(meta_from_name or {}).get("domain"),
                    name_begin_ts=(meta_from_name or {}).get("begin_ts"),
                    name_end_ts=(meta_from_name or {}).get("end_ts"))
                continue

            meta = parsed["meta"]; policy = parsed["policy"]; recs = parsed["records"]
            # If filename didn't provide epochs, use the XML meta range
            if final_begin is None:
                final_begin = meta.get("begin")
            if final_end is None:
                final_end = meta.get("end")
            fp_report = hashlib.sha256( (f"{meta.get('org_name','')}|{meta.get('report_id','')}|{meta.get('begin','')}|{meta.get('end','')}" ).encode("utf-8") ).hexdigest()
            store.upsert_report(conn, fp_report, meta, policy, sha)
            store.insert_records(conn, fp_report, meta.get("end"), recs)
            store.upsert_seen_ext(conn,
                file_path=name, size=st.st_size, mtime=st.st_mtime, sha256_xml=sha, status="parsed",
                origin_name=origin_name,
                name_provider=(meta_from_name or {}).get("provider"),
                name_domain=(meta_from_name or {}).get("domain"),
                name_begin_ts=final_begin,
                name_end_ts=final_end)
            store.log_decision(conn, session_id, name, "parsed", None)

        if not had_xml and not triples:
            store.log_decision(conn, session_id, fpath, "ignored:unknown", None)

    summary = store.ingest_report_summary(conn, session_id)
    return {"session_id": session_id, "summary": summary}


def ingest_bytes(data: bytes, filename: str, db_path: str, client_key: str) -> dict:
    """
    Write `data` to a temporary directory under the original `filename` and
    run the existing ingest pipeline against it.

    Using the original filename preserves DMARC filename-metadata parsing
    (provider!domain!begin!end.zip/gz/xml pattern).

    Returns the ingest summary dict: {parsed: N, dup_xml: N, ...}
    """
    tmp_dir = tempfile.mkdtemp(prefix="dmarcparser_")
    try:
        tmp_path = os.path.join(tmp_dir, filename)
        with open(tmp_path, "wb") as f:
            f.write(data)
        result = ingest(tmp_path, db_path, client_key)
        return result.get("summary", result)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
