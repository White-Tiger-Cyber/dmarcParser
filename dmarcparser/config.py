
import os

DEFAULTS = {
    "paths": {
        "client_db_dir": os.path.expanduser("~/.dmarcParser/clients"),
        "index_db":      os.path.expanduser("~/.dmarcParser/index.db"),
        "credentials":   os.path.expanduser("~/.dmarcParser/credentials"),
    },
    "ingest": {
        "extra_ignore_globs": [],
    },
    "gmail": {
        "delegated_user": "dmarcparser@whitetigercyber.com",
    },
    "agent": {
        "poll_interval": 15,      # minutes
        "verbosity":     "structured",  # quiet | structured | full
    },
}

def load_config():
    return DEFAULTS
