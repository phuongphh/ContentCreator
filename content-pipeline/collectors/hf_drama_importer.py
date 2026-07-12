from __future__ import annotations

"""
HuggingFace Drama Dataset Importer (issue #78 follow-up) — bulk seeding.

Reddit's live API is approval-gated post-Nov-2025, but public AITA/relationship
datasets already scraped from Reddit are freely downloadable on HuggingFace. This
imports one such dataset into the `stories` table (track='drama') in bulk, giving
the pipeline hundreds/thousands of stories to score → rewrite → render without
any live Reddit access.

Uses the HuggingFace datasets-server REST API (stdlib urllib only — no `datasets`
or `pandas` dependency):
    GET https://datasets-server.huggingface.co/rows
        ?dataset=<owner/name>&config=<cfg>&split=<split>&offset=<n>&length=<=100
Response: {"features":[{"name":...}], "rows":[{"row":{...}}], "num_rows_total":N}

This is a MANUAL, occasional tool (a 270K-row dataset shouldn't re-import daily):
    python -m collectors.hf_drama_importer [--dataset X] [--limit N] [--offset M]
Idempotent: source_id is derived from a stable row id (or a content hash), so
re-running skips already-imported rows via the stories unique index.

LICENSE NOTE: these datasets redistribute Reddit content. Using them as raw
material for a transform-heavy pipeline (drama_rewriter rewrites/localizes) is the
intended use, but check each dataset's license/terms before relying on it.
"""

import argparse
import hashlib
import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.stories import insert_story, dedupe_check

logger = logging.getLogger(__name__)

_ROWS_URL = "https://datasets-server.huggingface.co/rows"
_PAGE = 100  # datasets-server caps a page at 100 rows.

# Candidate column names, most-specific first, used when HF_TITLE_FIELD /
# HF_BODY_FIELD aren't set. AITA/relationship datasets vary in naming.
_TITLE_CANDIDATES = ["title", "post_title", "submission_title", "name", "header"]
_BODY_CANDIDATES = [
    "body", "selftext", "text", "post_text", "submission_text",
    "content", "story", "self_text",
]
_ID_CANDIDATES = ["id", "post_id", "submission_id", "name", "link_id"]
_REMOVED_SENTINELS = {"[removed]", "[deleted]", ""}


class HFImportError(Exception):
    """Raised when the dataset can't be read or no usable text column is found."""


def _fetch_rows(dataset: str, cfg: str, split: str, offset: int, length: int) -> dict:
    """GET one page from the datasets-server. Raises HFImportError on failure."""
    query = urlencode({
        "dataset": dataset, "config": cfg, "split": split,
        "offset": offset, "length": length,
    })
    url = f"{_ROWS_URL}?{query}"
    last_error = None
    for attempt in range(3):
        req = Request(url)
        req.add_header("User-Agent", "ai5phut-content-pipeline/1.0")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=config.HF_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except HTTPError as e:
            last_error = e
            # 404 = wrong dataset/config/split; retrying won't help.
            if e.code == 404:
                raise HFImportError(
                    f"dataset/config/split not found: {dataset} {cfg}/{split} "
                    f"(check names; set HF_DRAMA_CONFIG/HF_DRAMA_SPLIT)"
                ) from e
            logger.warning("HF HTTP %s at offset %d (attempt %d/3)", e.code, offset, attempt + 1)
        except (URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
            logger.warning("HF request error at offset %d (attempt %d/3): %s", offset, attempt + 1, e)
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))
    raise HFImportError(f"failed to fetch rows at offset {offset}: {last_error}")


def _pick_column(columns: list[str], override: str, candidates: list[str]) -> str | None:
    """Choose a column: explicit override if valid, else first present candidate."""
    if override:
        if override in columns:
            return override
        raise HFImportError(f"configured column {override!r} not in dataset columns {columns}")
    for cand in candidates:
        if cand in columns:
            return cand
    return None


def _row_source_id(dataset: str, row: dict, id_field: str | None, title: str, body: str) -> str:
    """Stable, idempotent source_id for a row.

    Prefers the dataset's own id column; falls back to a hash of title+body so
    re-imports of a dataset without an id still dedupe instead of duplicating.
    """
    tag = dataset.split("/")[-1][:20]
    if id_field and row.get(id_field) not in (None, ""):
        raw = str(row[id_field])
    else:
        raw = hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest()[:16]
    return f"hf_{tag}_{raw}"


def import_dataset(dataset: str | None = None, config_name: str | None = None,
                   split: str | None = None, limit: int | None = None,
                   offset: int = 0) -> int:
    """Import up to `limit` rows into `stories` (track='drama'). Returns new count."""
    dataset = dataset or config.HF_DRAMA_DATASET
    cfg = config_name or config.HF_DRAMA_CONFIG
    split = split or config.HF_DRAMA_SPLIT
    limit = config.HF_IMPORT_LIMIT if limit is None else limit

    first = _fetch_rows(dataset, cfg, split, offset, min(_PAGE, limit))
    columns = [f["name"] for f in first.get("features", []) if isinstance(f, dict) and "name" in f]
    if not columns:
        raise HFImportError(f"no feature columns reported for {dataset}")

    title_field = _pick_column(columns, config.HF_TITLE_FIELD, _TITLE_CANDIDATES)
    body_field = _pick_column(columns, config.HF_BODY_FIELD, _BODY_CANDIDATES)
    if not body_field:
        raise HFImportError(
            f"no text/body column found in {columns}; set HF_BODY_FIELD to the "
            f"column holding the story text"
        )
    id_field = _pick_column(columns, "", _ID_CANDIDATES)
    logger.info("HF import %s: title=%r body=%r id=%r (%d columns)",
                dataset, title_field, body_field, id_field, len(columns))

    total_available = first.get("num_rows_total", limit)
    target = min(limit, max(0, total_available - offset)) if total_available else limit

    imported = 0
    skipped_dup = skipped_empty = 0
    page = first
    fetched = 0
    while imported < target:
        rows = page.get("rows", [])
        if not rows:
            break
        for entry in rows:
            if imported >= target:
                break
            row = entry.get("row", {}) if isinstance(entry, dict) else {}
            title = str(row.get(title_field, "") or "").strip() if title_field else ""
            body = str(row.get(body_field, "") or "").strip()
            if not body or body in _REMOVED_SENTINELS:
                skipped_empty += 1
                continue
            source_id = _row_source_id(dataset, row, id_field, title, body)
            if dedupe_check(source_id):
                skipped_dup += 1
                continue
            insert_story(
                source="huggingface",
                source_id=source_id,
                raw_content=body,
                track="drama",
                title=title or None,
                metadata={"dataset": dataset, "hf_split": split},
            )
            imported += 1

        fetched += len(rows)
        next_offset = offset + fetched
        if imported >= target or next_offset >= (total_available or next_offset):
            break
        page = _fetch_rows(dataset, cfg, split, next_offset, min(_PAGE, target - imported))

    logger.info("HF import done: %d new stories (%d dup, %d empty) from %s",
                imported, skipped_dup, skipped_empty, dataset)
    return imported


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Bulk-import a HuggingFace AITA/drama dataset")
    parser.add_argument("--dataset", default=None, help="owner/name (default config.HF_DRAMA_DATASET)")
    parser.add_argument("--config", dest="config_name", default=None, help="dataset config/subset")
    parser.add_argument("--split", default=None, help="dataset split (default train)")
    parser.add_argument("--limit", type=int, default=None, help="max rows to import")
    parser.add_argument("--offset", type=int, default=0, help="start row offset")
    args = parser.parse_args()

    from storage.database import init_db
    from storage.migrate import migrate_up
    init_db()
    migrate_up()
    import_dataset(dataset=args.dataset, config_name=args.config_name,
                   split=args.split, limit=args.limit, offset=args.offset)
