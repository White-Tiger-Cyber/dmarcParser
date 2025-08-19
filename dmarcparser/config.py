
import os

DEFAULTS = {
    "paths": {
        "client_db_dir": os.path.expanduser("~/.dmarcParser/clients"),
    },
    "ingest": {
        "extra_ignore_globs": [],
    }
}

def load_config():
    return DEFAULTS
