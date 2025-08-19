
import os, io, gzip, zipfile, hashlib, re, json
from . import store
from .xmlparse import parse_rua_xml

XML_DECL_RE = re.compile(br'\s*<\?xml', re.I)

def discover_files(root):
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            lower = name.lower()
            if lower.endswith((".xml", ".gz", ".zip")):
                yield os.path.join(dirpath, name)

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
    session_id = store.start_session(conn, root_path, client_key, json.dumps({"rescan":rescan,"dry_run":dry_run}))

    for fpath in ( [root_path] if os.path.isfile(root_path) else discover_files(root_path) ):
        try: st = os.stat(fpath)
        except FileNotFoundError: continue
        seen = store.get_seen(conn, fpath)
        if (not rescan) and seen and seen["size"]==st.st_size and abs(seen["mtime"]-st.st_mtime) < 0.0001:
            store.log_decision(conn, session_id, fpath, "skipped_unchanged", None); continue

        triples = classify_and_extract(fpath)
        had_xml = False
        for kind, name, data in triples:
            if kind.startswith("ignored:"):
                store.log_decision(conn, session_id, name, kind, None); continue
            had_xml = True
            sha = canonical_xml_hash(data)
            cur = conn.execute("SELECT 1 FROM files_seen WHERE sha256_xml=?", (sha,))
            if cur.fetchone():
                store.log_decision(conn, session_id, name, "dup_xml", None); continue

            if dry_run:
                store.log_decision(conn, session_id, name, "would_parse", None)
                store.upsert_seen(conn, fpath, st.st_size, st.st_mtime, sha, "would_parse")
                continue

            try:
                parsed = parse_rua_xml(data)
            except Exception as e:
                store.log_decision(conn, session_id, name, "error_parse", str(e))
                store.upsert_seen(conn, fpath, st.st_size, st.st_mtime, sha, "error_parse")
                continue

            meta = parsed["meta"]; policy = parsed["policy"]; recs = parsed["records"]
            fp_report = hashlib.sha256( (f"{meta.get('org_name','')}|{meta.get('report_id','')}|{meta.get('begin','')}|{meta.get('end','')}" ).encode("utf-8") ).hexdigest()
            store.upsert_report(conn, fp_report, meta, policy, sha)
            store.insert_records(conn, fp_report, meta.get("end"), recs)
            store.upsert_seen(conn, fpath, st.st_size, st.st_mtime, sha, "parsed")
            store.log_decision(conn, session_id, name, "parsed", None)

        if not had_xml and not triples:
            store.log_decision(conn, session_id, fpath, "ignored:unknown", None)

    summary = store.ingest_report_summary(conn, session_id)
    return {"session_id": session_id, "summary": summary}
