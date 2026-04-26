#!/usr/bin/env python3
"""Generate INDEX.md files for docs/issues/ folder."""
import argparse, re, sys
from pathlib import Path
from datetime import datetime

try:
    import yaml
except ImportError:
    print("Run: pip install pyyaml"); sys.exit(1)

ISSUES_DIR = Path("docs/issues")

def parse_frontmatter(content):
    if not content.startswith("---\n"): return {}, content
    try:
        _, fm, body = content.split("---\n", 2)
        return yaml.safe_load(fm) or {}, body
    except: return {}, content

def parse_issue_file(path):
    try: content = path.read_text(encoding="utf-8")
    except: return {}

    meta, body = parse_frontmatter(content)

    if "issue_number" not in meta:
        m = re.match(r"issue-(\d+)\.md", path.name)
        if m: meta["issue_number"] = int(m.group(1))

    if "title" not in meta:
        m = re.search(r"^#\s+(.+?)$", body or content, re.MULTILINE)
        if m: meta["title"] = m.group(1).strip()

    if "phase" not in meta:
        for part in path.parts:
            if re.match(r"phase-[0-9a-z]+", part):
                meta["phase"] = part; break

    try: meta["_path"] = str(path.relative_to(ISSUES_DIR))
    except: meta["_path"] = str(path)
    return meta

def gen_index(directory, title, sort_desc=True):
    issues = []
    for f in sorted(directory.rglob("issue-*.md")):
        m = parse_issue_file(f)
        if m.get("issue_number"): issues.append(m)

    issues.sort(key=lambda x: x.get("issue_number", 0), reverse=sort_desc)

    by_phase = {}
    for i in issues:
        by_phase.setdefault(i.get("phase", "unknown"), []).append(i)

    lines = [
        f"# {title}", "",
        f"> Auto-generated • Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> Total: **{len(issues)}** issues", "",
    ]

    if not issues:
        lines.append("_No issues yet._")
        return "\n".join(lines)

    for phase in sorted(by_phase.keys()):
        items = by_phase[phase]
        lines += [
            f"## {phase} ({len(items)})", "",
            "| # | Title | Date | File |", "|---|-------|------|------|",
        ]
        for i in items:
            num = i.get("issue_number", "?")
            t = str(i.get("title", "_(no title)_")).replace("|", "\\|")
            d = i.get("closed_at") or i.get("updated_at", "")
            if isinstance(d, datetime): d = d.strftime("%Y-%m-%d")
            elif d: d = str(d).split("T")[0]
            lines.append(f"| #{num} | {t} | {d} | [view]({i.get('_path', '')}) |")
        lines.append("")
    return "\n".join(lines)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not ISSUES_DIR.exists():
        print(f"{ISSUES_DIR} not found"); sys.exit(1)

    for sub, title, desc in [("closed", "Closed Issues", True), ("active", "Active Issues", False)]:
        d = ISSUES_DIR / sub
        if d.exists():
            content = gen_index(d, title, sort_desc=desc)
            out = d / "INDEX.md"
            if args.dry_run:
                print(f"=== {out} ===\n{content}\n")
            else:
                out.write_text(content, encoding="utf-8")
                print(f"OK  {out}")

if __name__ == "__main__": main()
