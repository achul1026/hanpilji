import os
import tomllib
from pathlib import Path


def load(path="config.toml"):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["db"]["dsn"] = os.environ.get("HANPILJI_DSN", cfg["db"]["dsn"])
    cfg["law"]["oc"] = os.environ.get("LAW_OC", cfg["law"]["oc"])
    base = Path(path).resolve().parent
    cfg["storage"]["raw_dir"] = str((base / cfg["storage"]["raw_dir"]).resolve())
    return cfg
