"""
Gmail API wrapper for the DMARC agent.
All functions accept a `service` object built once at startup via build_service().
"""
import os
import glob
import base64

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# MIME types that may carry DMARC report attachments
_DMARC_MIMES = {
    "application/zip",
    "application/gzip",
    "application/x-gzip",
    "application/xml",
    "text/xml",
    "application/octet-stream",
}
_DMARC_EXTS = (".xml", ".gz", ".zip")


def build_service(creds_path=None, delegated_user=None):
    """
    Build and return an authenticated Gmail API service.
    creds_path: path to service-account JSON key.  If None, globs
                ~/.dmarcParser/credentials/*.json and uses the first match.
    delegated_user: the inbox address to impersonate.
    """
    if creds_path is None:
        pattern = os.path.expanduser("~/.dmarcParser/credentials/*.json")
        matches = glob.glob(pattern)
        if not matches:
            raise FileNotFoundError(
                f"No service-account credentials found at {pattern}. "
                "Place the GCP JSON key under ~/.dmarcParser/credentials/"
            )
        creds_path = sorted(matches)[0]

    creds = service_account.Credentials.from_service_account_file(
        creds_path, scopes=SCOPES
    )
    if delegated_user:
        creds = creds.with_subject(delegated_user)

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def ensure_label(service, name):
    """
    Return the label ID for `name`, creating the label if it does not exist.
    """
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for lbl in labels:
        if lbl["name"] == name:
            return lbl["id"]
    created = service.users().labels().create(
        userId="me", body={"name": name}
    ).execute()
    return created["id"]


def list_unprocessed_messages(service, batch_size=15):
    """
    Return up to `batch_size` message stubs (dicts with 'id', 'threadId')
    from INBOX that do NOT carry the 'dmarc-processed' label.
    Capped per call so the agent context stays manageable.
    """
    result = service.users().messages().list(
        userId="me",
        labelIds=["INBOX"],
        q="-label:dmarc-processed",
        maxResults=batch_size,
    ).execute()
    return result.get("messages", [])


def get_message_info(service, message_id):
    """
    Return {id, subject, sender, date} from a message's headers.
    """
    msg = service.users().messages().get(
        userId="me",
        id=message_id,
        format="metadata",
        metadataHeaders=["Subject", "From", "Date"],
    ).execute()
    hdrs = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    return {
        "id": message_id,
        "subject": hdrs.get("Subject", ""),
        "sender": hdrs.get("From", ""),
        "date": hdrs.get("Date", ""),
    }


def get_attachments(service, message_id):
    """
    Return a list of dicts: {filename, mime_type, data (bytes)}.
    Recursively walks multipart structures to find all attachment parts.
    """
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()
    results = []
    _collect_parts(service, message_id, msg["payload"], results)
    return results


def _collect_parts(service, message_id, payload, out):
    """Recursive helper: walk payload parts and collect DMARC attachments."""
    mime = payload.get("mimeType", "")
    parts = payload.get("parts", [])

    # Recurse into multipart containers
    if mime.startswith("multipart/"):
        for part in parts:
            _collect_parts(service, message_id, part, out)
        return

    filename = payload.get("filename", "")
    body = payload.get("body", {})

    # Decide if this part is a DMARC attachment
    is_dmarc = bool(filename) and (
        mime in _DMARC_MIMES
        or any(filename.lower().endswith(ext) for ext in _DMARC_EXTS)
    )
    if not is_dmarc:
        return

    # Fetch bytes
    att_id = body.get("attachmentId")
    if att_id:
        att = service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=att_id
        ).execute()
        data = base64.urlsafe_b64decode(att["data"])
    elif body.get("data"):
        data = base64.urlsafe_b64decode(body["data"])
    else:
        return

    out.append({"filename": filename, "mime_type": mime, "data": data})


def apply_label(service, message_id, label_id):
    """Apply a label to a message."""
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": [label_id]},
    ).execute()


def mark_as_read(service, message_id):
    """Remove the UNREAD label from a message."""
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()
