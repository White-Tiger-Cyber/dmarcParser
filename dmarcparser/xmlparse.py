
from xml.etree import ElementTree as ET

def _strip(tag):
    return tag.split('}',1)[1] if '}' in tag else tag

def _text(node, name, default=None):
    if node is None: return default
    for c in node:
        if _strip(c.tag) == name:
            return c.text.strip() if (c.text is not None) else default
    return default

def parse_rua_xml(xml_bytes):
    root = ET.fromstring(xml_bytes)
    if _strip(root.tag) != "feedback":
        raise ValueError("Not a DMARC feedback report")

    rm = pp = None
    records = []
    for child in root:
        tag = _strip(child.tag)
        if tag == "report_metadata":
            rm = child
        elif tag == "policy_published":
            pp = child
        elif tag == "record":
            row = identifiers = auth_results = None
            for k in child:
                stag = _strip(k.tag)
                if stag == "row": row = k
                elif stag == "identifiers": identifiers = k
                elif stag == "auth_results": auth_results = k

            source_ip = _text(row, "source_ip", "")
            try: count = int(_text(row, "count", "1"))
            except Exception: count = 1
            pe = None
            for k in (row or []):
                if _strip(k.tag) == "policy_evaluated": pe = k; break
            disposition = _text(pe, "disposition", None)
            dkim_result = _text(pe, "dkim", None)
            spf_result = _text(pe, "spf", None)

            envelope_from = header_from = envelope_to = None
            if identifiers is not None:
                envelope_from = _text(identifiers, "envelope_from", None) or _text(identifiers, "mail_from", None)
                header_from = _text(identifiers, "header_from", None)
                envelope_to = _text(identifiers, "envelope_to", None)

            spf_domain = spf_scope = None
            dkim_results = []
            if auth_results is not None:
                for elt in auth_results:
                    s = _strip(elt.tag)
                    if s == "spf":
                        if spf_domain is None:
                            spf_domain = _text(elt, "domain", None)
                            spf_scope  = _text(elt, "scope", None)
                    elif s == "dkim":
                        drec = {"domain": _text(elt, "domain", None),
                                "selector": _text(elt, "selector", None),
                                "result": _text(elt, "result", None)}
                        dkim_results.append(drec)

            records.append({
                "source_ip": source_ip,
                "count": count,
                "disposition": disposition,
                "dkim_result": dkim_result,
                "spf_result": spf_result,
                "envelope_from": envelope_from,
                "header_from": header_from,
                "envelope_to": envelope_to,
                "spf_domain": spf_domain,
                "spf_scope": spf_scope,
                "dkim_results": dkim_results
            })

    begin = end = None
    org_name = email = report_id = None
    if rm is not None:
        org_name = _text(rm, "org_name", None)
        email = _text(rm, "email", None)
        report_id = _text(rm, "report_id", None)
        dr = None
        for k in rm:
            if _strip(k.tag) == "date_range": dr = k; break
        if dr is not None:
            try: begin = int(_text(dr, "begin", "0"))
            except Exception: begin = None
            try: end = int(_text(dr, "end", "0"))
            except Exception: end = None

    policy = {}
    if pp is not None:
        policy = {
            "domain": _text(pp, "domain", None),
            "adkim": _text(pp, "adkim", None),
            "aspf":  _text(pp, "aspf", None),
            "p":     _text(pp, "p", None),
            "sp":    _text(pp, "sp", None),
            "pct":   _text(pp, "pct", None),
            "fo":    _text(pp, "fo", None),
            "np":    _text(pp, "np", None),
        }
    meta = {"org_name": org_name, "email": email, "report_id": report_id, "begin": begin, "end": end}
    return {"meta": meta, "policy": policy, "records": records}
