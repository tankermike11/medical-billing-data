"""Base pipeline: load source registry → download → hash-check → run ingestor."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from .db import get_conn
from .http import download, _sha256

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "source_registry.yaml"
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


def load_registry() -> dict:
    with open(REGISTRY_PATH) as f:
        return {s["source_id"]: s for s in yaml.safe_load(f)["sources"]}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_release(
    conn: sqlite3.Connection,
    source_id: str,
    source_url: str,
    file_hash: str,
    row_count: int,
    status: str = "success",
    notes: str = "",
) -> int:
    cur = conn.execute(
        """INSERT INTO dataset_releases
           (source_id, source_url, file_hash, row_count, status, notes, retrieved_at)
           VALUES (?,?,?,?,?,?,?)""",
        (source_id, source_url, file_hash, row_count, status, notes, now_utc()),
    )
    conn.commit()
    return cur.lastrowid


def record_error(conn: sqlite3.Connection, source_id: str, release_id: int | None, error_type: str, message: str) -> None:
    conn.execute(
        "INSERT INTO extraction_errors (source_id, release_id, error_type, message, occurred_at) VALUES (?,?,?,?,?)",
        (source_id, release_id, error_type, message, now_utc()),
    )
    conn.commit()


def fetch_source(source: dict) -> tuple[Path, str]:
    """Download source file to data/raw/<source_id>/. Returns (local_path, sha256)."""
    source_id = source["source_id"]
    url = source["canonical_url"]
    filename = url.split("/")[-1].split("?")[0] or f"{source_id}.bin"
    dest = RAW_DIR / source_id / filename
    return download(url, dest)


def run_ingestor(source_id: str, ingestor_fn: Callable[[dict, Path, str, sqlite3.Connection], int]) -> None:
    registry = load_registry()
    if source_id not in registry:
        raise KeyError(f"Unknown source_id: {source_id}")
    source = registry[source_id]
    conn = get_conn()

    print(f"[{source_id}] fetching {source['canonical_url']}")
    try:
        local_path, file_hash = fetch_source(source)
    except Exception as e:
        record_error(conn, source_id, None, "fetch_error", str(e))
        raise

    last = conn.execute(
        "SELECT file_hash FROM dataset_releases WHERE source_id=? AND status='success' ORDER BY id DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    if last and last["file_hash"] == file_hash:
        print(f"[{source_id}] unchanged (hash match) — skipping parse")
        return

    print(f"[{source_id}] parsing {local_path}")
    release_id = None
    try:
        row_count = ingestor_fn(source, local_path, file_hash, conn)
        release_id = record_release(conn, source_id, source["canonical_url"], file_hash, row_count)
        print(f"[{source_id}] done — {row_count} rows (release #{release_id})")
    except Exception as e:
        record_error(conn, source_id, release_id, "parse_error", str(e))
        raise
