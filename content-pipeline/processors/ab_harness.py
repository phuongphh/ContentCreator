from __future__ import annotations

"""
A/B harness — chọn version prompt theo split ổn định (deterministic) theo
story_id, ghi kết quả heuristic để so sánh sau khi đủ mẫu (Phase 3 EPIC #3.4).

**Thiết kế rút gọn so với phase-3-issues.md**: bản gốc đề xuất hạ tầng chia
traffic 50/50 đầy đủ. `choose_version()` ở đây chỉ hash `(experiment,
story_id)` — không cần state lưu trữ riêng cho việc chia traffic, và quan
trọng hơn: CÙNG 1 story luôn ra CÙNG 1 version dù gọi lại nhiều lần (scorer
và rewriter của cùng 1 story nên dùng nhất quán 1 version; retry sau lỗi
không được đổi version giữa chừng — random thuần túy sẽ vi phạm cả 2 điều
này).
"""

import hashlib
import logging

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from storage.ab_runs import record_run, get_runs

logger = logging.getLogger(__name__)

DEFAULT_VERSIONS = ("v1", "v2")
MIN_SAMPLES_TO_COMPARE = 10


def choose_version(experiment: str, story_id: int,
                   versions: tuple[str, ...] = DEFAULT_VERSIONS) -> str:
    """Deterministically assign one of `versions` to (experiment, story_id).

    Same inputs always produce the same output — this is a hash lookup, not
    a random draw — so repeated/retried calls for one story stay consistent.
    """
    key = f"{experiment}:{story_id}".encode("utf-8")
    digest = hashlib.sha256(key).hexdigest()
    index = int(digest, 16) % len(versions)
    return versions[index]


def record_ab_result(experiment: str, version: str, story_id: int,
                     heuristic_score: float) -> None:
    """Record one experiment outcome for later comparison via compare_ab_results()."""
    record_run(experiment, version, story_id, heuristic_score)
    logger.info(
        "Recorded A/B result: experiment=%s version=%s story=%s score=%s",
        experiment, version, story_id, heuristic_score,
    )


def compare_ab_results(experiment: str,
                       min_samples: int = MIN_SAMPLES_TO_COMPARE) -> dict | None:
    """Compare mean heuristic_score per version recorded for `experiment`.

    Returns None if there are no runs yet, or any version present has fewer
    than `min_samples` runs (too early to conclude anything). Otherwise:
        {"<version>": {"n": int, "mean": float}, ..., "better": "<version>"|"tie"}
    """
    runs = get_runs(experiment)
    if not runs:
        return None

    by_version: dict[str, list[float]] = {}
    for run in runs:
        score = run.get("heuristic_score")
        if score is None:
            continue
        by_version.setdefault(run["version"], []).append(score)

    if not by_version or any(len(scores) < min_samples for scores in by_version.values()):
        return None

    summary: dict = {
        version: {"n": len(scores), "mean": sum(scores) / len(scores)}
        for version, scores in by_version.items()
    }

    means = {version: stats["mean"] for version, stats in summary.items()}
    if len(set(means.values())) == 1:
        summary["better"] = "tie"
    else:
        summary["better"] = max(means, key=means.get)

    return summary
