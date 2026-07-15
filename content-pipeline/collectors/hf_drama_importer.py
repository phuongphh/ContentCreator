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

RAW-CSV FALLBACK (issue #92): datasets-server is a separate service that
periodically 503s / serves a "viewer building" HTML page for big datasets. When
that happens the API path raises HFDatasetUnavailableError and we fall back to
downloading the dataset's raw CSV from the Hub (which stays up) via Git LFS
    GET https://huggingface.co/datasets/<owner/name>/resolve/main/<file>.csv
caching it once on disk (the dump is static — see HF_DRAMA_* in config). The CSV
is read with the SAME column auto-detection and source_id scheme as the API path,
so the daily cursor offset and dedupe stay consistent across an API↔CSV switch.

This is a MANUAL, occasional tool (a 270K-row dataset shouldn't re-import daily):
    python -m collectors.hf_drama_importer [--dataset X] [--limit N] [--offset M]
Idempotent: source_id is derived from a stable row id (or a content hash), so
re-running skips already-imported rows via the stories unique index.

LICENSE NOTE: these datasets redistribute Reddit content. Using them as raw
material for a transform-heavy pipeline (drama_rewriter rewrites/localizes) is the
intended use, but check each dataset's license/terms before relying on it.
"""

import argparse
import csv
import hashlib
import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from storage.stories import insert_story, dedupe_check

logger = logging.getLogger(__name__)

_ROWS_URL = "https://datasets-server.huggingface.co/rows"
_PAGE = 100  # datasets-server caps a page at 100 rows.

# Raw-CSV fallback (issue #92). The Hub stays up when datasets-server is down.
_HUB_API_URL = "https://huggingface.co/api/datasets/"          # + <owner/name>
_RESOLVE_URL = "https://huggingface.co/datasets/{ds}/resolve/main/{path}"
_CSV_DOWNLOAD_CHUNK = 1 << 20  # 1 MiB streaming chunks

# AITA bodies routinely exceed csv's default 128 KiB field cap; raise it so a
# long story doesn't abort the whole read with "field larger than field limit".
csv.field_size_limit(16 * 1024 * 1024)

# Candidate column names, most-specific first, used when HF_TITLE_FIELD /
# HF_BODY_FIELD aren't set. AITA/relationship datasets vary in naming.
_TITLE_CANDIDATES = ["title", "post_title", "submission_title", "name", "header"]
_BODY_CANDIDATES = [
    "body", "selftext", "text", "post_text", "submission_text",
    "content", "story", "self_text",
]
_ID_CANDIDATES = ["id", "post_id", "submission_id", "name", "link_id"]
# Comment/reaction columns, most-specific first (issue #92 follow-up). AITA dumps
# vary: some ship a single top comment, some a JSON list, some scored dicts.
_COMMENT_CANDIDATES = [
    "top_comments", "top_comment", "best_comment", "comments", "comment",
    "comment_body", "selftext_comments", "body_comments",
]
_REMOVED_SENTINELS = {"[removed]", "[deleted]", ""}
# Keys a comment dict might use for its text / score, across dataset conventions.
_COMMENT_TEXT_KEYS = ("body", "content", "text", "comment", "comment_body")
_COMMENT_SCORE_KEYS = ("score", "ups", "upvotes", "num_upvotes")


class HFImportError(Exception):
    """Raised when the dataset can't be read or no usable text column is found."""


class HFDatasetUnavailableError(HFImportError):
    """The datasets-server can't currently serve this dataset's rows — the viewer
    is unavailable/still building (a non-JSON HTML gateway page, or a 5xx), as
    opposed to a config mistake (404) or a code bug.

    This is a SOFT, expected condition: the daily drama pipeline treats it as a
    warning (the deep-backfill cushion carries production) instead of a hard
    error that spams the morning summary. See main_drama's collect step."""


def _fetch_rows(dataset: str, cfg: str, split: str, offset: int, length: int) -> dict:
    """GET one page from the datasets-server. Raises on failure.

    Raises HFDatasetUnavailableError (a soft, retriable-tomorrow condition) when
    the server keeps returning a non-JSON body or a 5xx across all retries — the
    signature of a dataset whose viewer is unavailable/still building. Raises
    plain HFImportError for a 404 (wrong dataset/config/split) or other errors.
    """
    query = urlencode({
        "dataset": dataset, "config": cfg, "split": split,
        "offset": offset, "length": length,
    })
    url = f"{_ROWS_URL}?{query}"
    last_error = None
    unavailable = False  # last failure looked like "viewer down" (non-JSON / 5xx)
    for attempt in range(3):
        req = Request(url)
        req.add_header("User-Agent", "ai5phut-content-pipeline/1.0")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=config.HF_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except json.JSONDecodeError as e:
                # A non-JSON body (typically an HTML gateway/"viewer unavailable"
                # page → "Unexpected token '<'") means rows aren't servable now.
                last_error = e
                unavailable = True
                logger.warning("HF non-JSON response at offset %d (attempt %d/3): %.80s",
                               offset, attempt + 1, body.lstrip())
        except HTTPError as e:
            last_error = e
            # 404 = wrong dataset/config/split; retrying won't help.
            if e.code == 404:
                raise HFImportError(
                    f"dataset/config/split not found: {dataset} {cfg}/{split} "
                    f"(check names; set HF_DRAMA_CONFIG/HF_DRAMA_SPLIT)"
                ) from e
            # 5xx = server can't build/serve the dataset right now (viewer down).
            unavailable = e.code >= 500
            logger.warning("HF HTTP %s at offset %d (attempt %d/3)", e.code, offset, attempt + 1)
        except (URLError, TimeoutError) as e:
            last_error = e
            unavailable = False  # network blip, not a dataset-availability signal
            logger.warning("HF request error at offset %d (attempt %d/3): %s", offset, attempt + 1, e)
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))
    if unavailable:
        raise HFDatasetUnavailableError(
            f"datasets-server can't serve rows for {dataset} ({cfg}/{split}) — "
            f"viewer unavailable/building (last: {last_error}). Falling back to the "
            f"backfill cushion; run a deep backfill if the drama backlog runs low."
        )
    raise HFImportError(f"failed to fetch rows at offset {offset}: {last_error}")


def _dataset_size(dataset: str, cfg: str, split: str) -> int:
    """Total row count of a dataset split (a tiny 1-row probe). 0 if unknown."""
    page = _fetch_rows(dataset, cfg, split, 0, 1)
    return int(page.get("num_rows_total") or 0)


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


def _parse_comments(value) -> list[dict]:
    """Normalise a row's comment cell into [{content, score}], score None if absent.

    Handles the shapes AITA dumps use: a JSON list of strings, a JSON list of
    scored dicts, or a single plain-string top comment. Best-effort — anything
    unparseable degrades to a single-string comment rather than raising."""
    if value is None:
        return []
    if isinstance(value, (list, dict)):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return [{"content": text, "score": None}]  # one plain comment
    items = parsed if isinstance(parsed, list) else [parsed]
    out: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            content = next((str(item[k]) for k in _COMMENT_TEXT_KEYS
                            if item.get(k)), "").strip()
            score = next((item[k] for k in _COMMENT_SCORE_KEYS
                          if isinstance(item.get(k), (int, float))), None)
        else:
            content, score = str(item).strip(), None
        if content:
            out.append({"content": content, "score": score})
    return out


def _select_quality_comments(comments: list[dict]) -> list[str]:
    """Filter + rank comments the way the Lemmy Q&A collector does: drop
    removed/short/low-scored ones, best-score first, cap at HF_COMMENT_TOP_N.

    When a comment carries no score (dataset didn't store one) we can't score-gate
    it, so it's kept on length alone — better a good unscored reply than nothing."""
    good = []
    for c in comments:
        content = c["content"]
        if content in _REMOVED_SENTINELS or len(content) < config.HF_COMMENT_MIN_CHARS:
            continue
        score = c["score"]
        if score is not None and score < config.HF_COMMENT_MIN_SCORE:
            continue
        good.append(c)
    # Stable sort: scored comments by score desc; unscored keep insertion order
    # (datasets that ship a single/pre-ranked comment stay in their given order).
    good.sort(key=lambda c: c["score"] if c["score"] is not None else -1, reverse=True)
    return [c["content"] for c in good[:config.HF_COMMENT_TOP_N]]


def _row_comments(row: dict, comment_field: str | None) -> list[str]:
    """Quality comments for a row, or [] when comments are off / column absent."""
    if not (config.HF_IMPORT_COMMENTS and comment_field):
        return []
    return _select_quality_comments(_parse_comments(row.get(comment_field)))


def _compose_raw_content(body: str, comments: list[str]) -> str:
    """Body plus a labelled community-reaction section the scorer/rewriter can see.

    Kept in English (the source language) so it reads as part of the raw material
    the rewriter localises; the label makes it unambiguous these are Reddit
    reactions, not the OP's story."""
    if not comments:
        return body
    section = "\n\n---\nTOP COMMENTS FROM REDDIT:\n" + "\n".join(f"- {c}" for c in comments)
    return body + section


def _resolve_columns(columns: list[str], dataset: str
                     ) -> tuple[str | None, str, str | None, str | None]:
    """Pick (title, body, id, comment) columns for a dataset, shared by API and
    CSV paths.

    Raises HFImportError if no usable body/text column is present. Keeping this in
    one place is what guarantees the two row sources auto-detect identically, so a
    row's source_id (and thus dedupe) is the same however it was fetched."""
    if not columns:
        raise HFImportError(f"no columns reported for {dataset}")
    title_field = _pick_column(columns, config.HF_TITLE_FIELD, _TITLE_CANDIDATES)
    body_field = _pick_column(columns, config.HF_BODY_FIELD, _BODY_CANDIDATES)
    if not body_field:
        raise HFImportError(
            f"no text/body column found in {columns}; set HF_BODY_FIELD to the "
            f"column holding the story text"
        )
    id_field = _pick_column(columns, "", _ID_CANDIDATES)
    # Comment column is optional: auto-detect, but only when it isn't the body
    # column already claimed (some datasets name the body "comment").
    comment_field = None
    if config.HF_IMPORT_COMMENTS:
        comment_field = _pick_column(columns, config.HF_COMMENTS_FIELD, _COMMENT_CANDIDATES)
        if comment_field == body_field:
            comment_field = None
    return title_field, body_field, id_field, comment_field


def _import_row(dataset: str, row: dict, title_field: str | None, body_field: str,
                id_field: str | None, split: str, comment_field: str | None = None) -> str:
    """Insert one dataset row as a drama story. Returns 'imported'|'dup'|'empty'.

    The single source of truth for row → story mapping (source_id, dedupe,
    metadata, comment enrichment) so the API and CSV paths stay byte-for-byte
    consistent. source_id is derived from title+body ONLY (not comments), so a row
    dedupes identically whether or not its comments were available/loaded."""
    title = str(row.get(title_field, "") or "").strip() if title_field else ""
    body = str(row.get(body_field, "") or "").strip()
    if not body or body in _REMOVED_SENTINELS:
        return "empty"
    source_id = _row_source_id(dataset, row, id_field, title, body)
    if dedupe_check(source_id):
        return "dup"
    comments = _row_comments(row, comment_field)
    metadata = {"dataset": dataset, "hf_split": split}
    if comments:
        metadata["top_comments"] = comments
    insert_story(
        source="huggingface",
        source_id=source_id,
        raw_content=_compose_raw_content(body, comments),
        track="drama",
        title=title or None,
        metadata=metadata,
    )
    return "imported"


def _paginate_import(dataset: str, cfg: str, split: str, offset: int,
                     limit: int) -> tuple[int, int]:
    """Import up to `limit` rows starting at `offset`. Returns (imported, scanned).

    imported = new stories inserted; scanned = dataset rows consumed (INCLUDING
    empty/removed/duplicate ones) counted from `offset`. A caller tracking a
    forward cursor advances by exactly `scanned`, so skipped-empty rows aren't
    re-scanned next run. Shared by import_dataset (head/tail/manual offset) and
    import_daily (persisted cursor).
    """
    first = _fetch_rows(dataset, cfg, split, offset, min(_PAGE, limit))
    columns = [f["name"] for f in first.get("features", []) if isinstance(f, dict) and "name" in f]
    title_field, body_field, id_field, comment_field = _resolve_columns(columns, dataset)
    logger.info("HF import %s: title=%r body=%r id=%r comment=%r (%d columns)",
                dataset, title_field, body_field, id_field, comment_field, len(columns))

    total_available = first.get("num_rows_total", limit)
    target = min(limit, max(0, total_available - offset)) if total_available else limit

    imported = 0
    scanned = 0  # rows consumed from `offset`; exact even when we break mid-page
    skipped_dup = skipped_empty = 0
    page = first
    while imported < target:
        rows = page.get("rows", [])
        if not rows:
            break
        for entry in rows:
            if imported >= target:
                break
            scanned += 1
            row = entry.get("row", {}) if isinstance(entry, dict) else {}
            verdict = _import_row(dataset, row, title_field, body_field, id_field,
                                  split, comment_field)
            if verdict == "imported":
                imported += 1
            elif verdict == "dup":
                skipped_dup += 1
            else:
                skipped_empty += 1

        next_offset = offset + scanned
        if imported >= target or next_offset >= (total_available or next_offset):
            break
        page = _fetch_rows(dataset, cfg, split, next_offset, min(_PAGE, target - imported))

    logger.info("HF import done: %d new stories (%d dup, %d empty, %d scanned) from %s",
                imported, skipped_dup, skipped_empty, scanned, dataset)
    return imported, scanned


# --------------------------------------------------------------------------- #
# Raw-CSV fallback (issue #92): datasets-server down → read the Hub's raw CSV.
# --------------------------------------------------------------------------- #

def _hub_get(url: str) -> bytes:
    """GET a Hub URL, translating failures into the same soft/hard split the API
    path uses: network/5xx → HFDatasetUnavailableError (retry tomorrow); 404 →
    HFImportError (misconfig). Retries transient failures with backoff."""
    last_error = None
    for attempt in range(3):
        req = Request(url)
        req.add_header("User-Agent", "ai5phut-content-pipeline/1.0")
        try:
            with urlopen(req, timeout=config.HF_TIMEOUT) as resp:
                return resp.read()
        except HTTPError as e:
            last_error = e
            if e.code == 404:
                raise HFImportError(f"Hub resource not found (404): {url}") from e
            logger.warning("Hub HTTP %s for %s (attempt %d/3)", e.code, url, attempt + 1)
        except (URLError, TimeoutError) as e:
            last_error = e
            logger.warning("Hub request error for %s (attempt %d/3): %s", url, attempt + 1, e)
        if attempt < 2:
            time.sleep(2 ** (attempt + 1))
    raise HFDatasetUnavailableError(
        f"Hub unreachable for {url} (last: {last_error}); raw-CSV fallback failed"
    )


def _resolve_csv_file(dataset: str) -> str:
    """Repo path of the CSV to read. HF_DRAMA_CSV_FILE wins; else discover it via
    the Hub API (which stays up when datasets-server is down, per issue #92).

    Picks the first `.csv` sibling. Raises HFImportError if the repo exposes none
    (a real misconfig — surface it, don't silently soft-skip)."""
    if config.HF_DRAMA_CSV_FILE:
        return config.HF_DRAMA_CSV_FILE
    body = _hub_get(f"{_HUB_API_URL}{dataset}")
    try:
        meta = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise HFDatasetUnavailableError(f"Hub API non-JSON for {dataset}: {e}") from e
    csvs = [s.get("rfilename") for s in meta.get("siblings", [])
            if isinstance(s, dict) and str(s.get("rfilename", "")).lower().endswith(".csv")]
    if not csvs:
        raise HFImportError(
            f"no .csv file in {dataset}; set HF_DRAMA_CSV_FILE to the data file's "
            f"repo path (raw-CSV fallback needs a CSV)"
        )
    logger.info("HF CSV fallback: using %r from %s (%d csv files)", csvs[0], dataset, len(csvs))
    return csvs[0]


def _cache_path(dataset: str, csv_file: str) -> str:
    """Local cache path for a dataset's CSV. The filename is hashed so an
    attacker-controlled repo path can't escape HF_CSV_CACHE_DIR (path traversal),
    with a readable prefix for humans browsing the cache dir."""
    safe = dataset.replace("/", "__")[:40]
    digest = hashlib.sha256(f"{dataset}\n{csv_file}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(config.HF_CSV_CACHE_DIR, f"{safe}__{digest}.csv")


def _cache_is_fresh(path: str) -> bool:
    if not (os.path.exists(path) and os.path.getsize(path) > 0):
        return False
    ttl_days = config.HF_CSV_CACHE_TTL_DAYS
    if ttl_days <= 0:
        return True  # static dump: cache never expires
    age_days = (time.time() - os.path.getmtime(path)) / 86400.0
    return age_days < ttl_days


def _download_csv(dataset: str, csv_file: str, dest: str) -> None:
    """Stream the raw CSV to `dest` atomically. Aborts if it exceeds
    HF_CSV_MAX_BYTES (disk-fill guard). Downloads to a temp file and renames so a
    crash mid-download never leaves a truncated CSV that later reads as valid."""
    url = _RESOLVE_URL.format(ds=dataset, path=quote(csv_file))
    os.makedirs(config.HF_CSV_CACHE_DIR, exist_ok=True)
    tmp = f"{dest}.part"
    written = 0
    logger.info("HF CSV fallback: downloading %s → %s", url, dest)
    req = Request(url)
    req.add_header("User-Agent", "ai5phut-content-pipeline/1.0")
    try:
        with urlopen(req, timeout=config.HF_TIMEOUT) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(_CSV_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > config.HF_CSV_MAX_BYTES:
                    raise HFImportError(
                        f"CSV for {dataset} exceeds HF_CSV_MAX_BYTES "
                        f"({config.HF_CSV_MAX_BYTES} bytes); raise the cap or pick a smaller file"
                    )
                out.write(chunk)
    except HTTPError as e:
        _safe_unlink(tmp)
        if e.code == 404:
            raise HFImportError(f"CSV not found (404): {url}") from e
        raise HFDatasetUnavailableError(f"Hub CSV download failed ({e.code}): {url}") from e
    except (URLError, TimeoutError) as e:
        _safe_unlink(tmp)
        raise HFDatasetUnavailableError(f"Hub CSV download error: {url} ({e})") from e
    except BaseException:
        _safe_unlink(tmp)
        raise
    os.replace(tmp, dest)
    logger.info("HF CSV fallback: cached %d bytes at %s", written, dest)


def _safe_unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _ensure_csv_cached(dataset: str) -> str:
    """Return a local path to the dataset's CSV, downloading (and caching) it if
    the cache is missing/stale. One download serves both the size probe and the
    row read, so a fallback run fetches the big file at most once."""
    csv_file = _resolve_csv_file(dataset)
    path = _cache_path(dataset, csv_file)
    if _cache_is_fresh(path):
        logger.info("HF CSV fallback: using cached %s", path)
        return path
    _download_csv(dataset, csv_file, path)
    return path


def _open_csv_reader(path: str):
    """Open the cached CSV as a DictReader, returning (file, reader, columns).
    Caller closes the file. Raises HFImportError if it has no header."""
    f = open(path, newline="", encoding="utf-8", errors="replace")
    reader = csv.DictReader(f)
    columns = reader.fieldnames or []
    if not columns:
        f.close()
        raise HFImportError(f"cached CSV {path} has no header row")
    return f, reader, list(columns)


def _csv_row_count(dataset: str) -> int:
    """Row count (excluding header) of the dataset's CSV — the CSV analogue of
    _dataset_size, for cursor wrap-around. Cheap on a local cached file."""
    path = _ensure_csv_cached(dataset)
    f, reader, _cols = _open_csv_reader(path)
    try:
        return sum(1 for _ in reader)
    finally:
        f.close()


def _paginate_import_csv(dataset: str, split: str, offset: int,
                         limit: int) -> tuple[int, int]:
    """CSV analogue of _paginate_import: import up to `limit` rows starting at
    row `offset` from the cached CSV. Returns (imported, scanned) with the SAME
    semantics — scanned counts rows consumed from `offset` including skipped ones,
    so the daily cursor advances identically whether the API or CSV served them.

    Row order matches the datasets-server (both read the same underlying file in
    file order), so an offset written by the API path resumes correctly here."""
    path = _ensure_csv_cached(dataset)
    f, reader, columns = _open_csv_reader(path)
    try:
        title_field, body_field, id_field, comment_field = _resolve_columns(columns, dataset)
        logger.info("HF CSV import %s: title=%r body=%r id=%r comment=%r (%d columns)",
                    dataset, title_field, body_field, id_field, comment_field, len(columns))
        imported = scanned = skipped_dup = skipped_empty = 0
        for i, row in enumerate(reader):
            if i < offset:
                continue
            if imported >= limit:
                break
            scanned += 1
            verdict = _import_row(dataset, row, title_field, body_field, id_field,
                                  split, comment_field)
            if verdict == "imported":
                imported += 1
            elif verdict == "dup":
                skipped_dup += 1
            else:
                skipped_empty += 1
    finally:
        f.close()
    logger.info("HF CSV import done: %d new stories (%d dup, %d empty, %d scanned) from %s",
                imported, skipped_dup, skipped_empty, scanned, dataset)
    return imported, scanned


def _import_window(dataset: str, cfg: str, split: str, offset: int,
                   limit: int, force_csv: bool = False) -> tuple[int, int]:
    """Import a window via the datasets-server API, falling back to the raw CSV
    when the API viewer is unavailable (issue #92). Single dispatch point so
    import_dataset and import_daily both get the fallback with identical cursor
    semantics. If the CSV fallback is disabled or itself unavailable, the soft
    HFDatasetUnavailableError propagates unchanged (main_drama soft-skips it).

    force_csv=True skips the API entirely (the `--csv` backfill path): read
    straight from the raw CSV even when the API is up."""
    if force_csv:
        return _paginate_import_csv(dataset, split, offset, limit)
    try:
        return _paginate_import(dataset, cfg, split, offset, limit)
    except HFDatasetUnavailableError:
        if not config.HF_CSV_FALLBACK_ENABLED:
            raise
        logger.warning("datasets-server unavailable for %s — falling back to raw CSV (issue #92)",
                       dataset)
        return _paginate_import_csv(dataset, split, offset, limit)


def _dataset_size_or_csv(dataset: str, cfg: str, split: str) -> int:
    """_dataset_size with the same CSV fallback as _import_window, so import_daily
    can compute cursor wrap-around while the API is down."""
    try:
        return _dataset_size(dataset, cfg, split)
    except HFDatasetUnavailableError:
        if not config.HF_CSV_FALLBACK_ENABLED:
            raise
        return _csv_row_count(dataset)


def import_dataset(dataset: str | None = None, config_name: str | None = None,
                   split: str | None = None, limit: int | None = None,
                   offset: int = 0, newest: bool = False,
                   force_csv: bool = False) -> int:
    """Import up to `limit` rows into `stories` (track='drama'). Returns new count.

    newest=True pulls from the TAIL of the dataset (offset = num_rows_total -
    limit) instead of the head — for append-updated datasets (e.g. the hourly
    AITA dataset) the tail is the freshest content, which is what "thời sự"
    wants. It costs one extra 1-row probe to learn the size. For the daily
    static-dump source use import_daily (a forward cursor), not newest.

    force_csv=True reads straight from the raw CSV (the `--csv` backfill path),
    bypassing the datasets-server API — useful when the API is known to be down
    (issue #92) or for a fast one-shot deep backfill.
    """
    dataset = dataset or config.HF_DRAMA_DATASET
    cfg = config_name or config.HF_DRAMA_CONFIG
    split = split or config.HF_DRAMA_SPLIT
    limit = config.HF_IMPORT_LIMIT if limit is None else limit

    if newest:
        total = (_csv_row_count(dataset) if force_csv
                 else _dataset_size_or_csv(dataset, cfg, split))
        offset = max(0, total - limit)
        logger.info("HF --newest: %s has %d rows → importing from offset %d",
                    dataset, total, offset)

    imported, _scanned = _import_window(dataset, cfg, split, offset, limit, force_csv=force_csv)
    return imported


def import_daily(dataset: str | None = None, limit: int | None = None) -> int:
    """Import the next unseen window of a STATIC dataset, tracked by a cursor.

    The reliable daily drama source (issue #90). Walks FORWARD through the dataset
    a fresh `limit`-row slice per call, persisting the offset in `pipeline_state`
    (migration 008) so the next run continues where this one stopped — unlike
    newest=True, it never re-imports the same tail against a static dump. When the
    cursor reaches the end it wraps back to 0 (a 270K-row dump at ~10/day is years
    of runway; wrap keeps it self-sustaining after that).

    dedupe_check still guards every row, so the cursor and a manual `--limit N`
    backfill coexist safely: rows the backfill already inserted are scanned,
    skipped as duplicates, and the cursor advances past them. If the fetch fails
    the exception propagates (cursor NOT advanced) so the next run retries the
    same window.
    """
    from storage.pipeline_state import get_int, set_int
    dataset = dataset or config.HF_DRAMA_DATASET
    cfg = config.HF_DRAMA_CONFIG
    split = config.HF_DRAMA_SPLIT
    limit = config.HF_DAILY_LIMIT if limit is None else limit

    # Key by dataset + config + split: _dataset_size/_paginate_import read a
    # different row stream per config, so a shared cursor across configs would
    # start a new config at a stale offset (skip its head, later collide).
    key = f"hf_cursor:{dataset}:{cfg}:{split}"
    total = _dataset_size_or_csv(dataset, cfg, split)
    offset = get_int(key, 0)
    if total and offset >= total:
        logger.info("HF cursor for %s past end (%d/%d) — wrapping to 0",
                    dataset, offset, total)
        offset = 0

    imported, scanned = _import_window(dataset, cfg, split, offset, limit)
    new_offset = offset + scanned
    if total and new_offset >= total:
        new_offset = 0  # wrapped; next run restarts from the top
    set_int(key, new_offset)
    logger.info("HF daily import: %d new from %s (cursor %d → %d of %d)",
                imported, dataset, offset, new_offset, total)
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
    parser.add_argument("--newest", action="store_true",
                        help="import the newest rows (tail) instead of from --offset")
    parser.add_argument("--csv", action="store_true",
                        help="read from the raw CSV (Git LFS) instead of the "
                             "datasets-server API — use when the API is down (issue #92)")
    args = parser.parse_args()

    from storage.database import init_db
    from storage.migrate import migrate_up
    init_db()
    migrate_up()
    import_dataset(dataset=args.dataset, config_name=args.config_name,
                   split=args.split, limit=args.limit, offset=args.offset,
                   newest=args.newest, force_csv=args.csv)
