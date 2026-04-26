# Project Documentation & Issue Workflow Template

> **Purpose:** This file describes a reusable documentation and issue management workflow that can be cloned across projects.
>
> **For AI agents (Claude Code, etc.):** Use this as an implementation specification. Read entirely before starting. Ask user for project-specific values when you encounter `{{PLACEHOLDER}}` markers.

---

## What You're Implementing

A documentation and issue management system with these properties:

- **Master files** describe high-level plans (strategy, phase guides)
- **Individual issue files** in markdown, synced bidirectionally with GitHub issues
- **Automated lifecycle**: GitHub Action moves files between `active/` and `closed/` folders
- **Phase organization**: Closed issues sorted by phase for findability
- **AI-friendly structure**: Each issue file is self-contained context for AI implementation
- **History preservation**: Pivots/changes archived, not deleted

## Workflow Summary

```
1. User maintains master file (e.g., phase-X-issues.md)
2. AI agent reads master → drafts individual issues
3. User reviews + approves
4. Agent pushes drafts to GitHub → creates issue #N
5. GitHub Action triggers → creates docs/issues/active/issue-N.md
6. User triggers AI: "implement issue N"
7. AI reads issue file + related docs → implements
8. PR merges → issue auto-closes
9. GitHub Action moves file to docs/issues/closed/by-phase/phase-X/
10. INDEX.md regenerates automatically
```

---

## Pre-Flight Questions

Before implementing, ask the user these questions if not provided:

1. **Project name** for documentation references → `{{PROJECT_NAME}}`
2. **Does the project already have a `docs/` folder with content?** 
   - If yes → migration mode (preserve existing content)
   - If no → greenfield mode (clean setup)
3. **Phase naming convention** (e.g., `phase-1`, `phase-3a`, `sprint-1`, `milestone-2`) → `{{PHASE_PATTERN}}`
4. **Default branch name** (`main` or `master`) → `{{DEFAULT_BRANCH}}`
5. **Will this project use the same OpenClaw Agent → GitHub flow?**
   - If yes → install full automation
   - If no → install structure only, skip GitHub Action

---

## Implementation Checklist

Implement in this exact order. Verify each step before proceeding.

### Step 1: Create Folder Structure

```bash
mkdir -p docs/current
mkdir -p docs/archive
mkdir -p docs/issues/active
mkdir -p docs/issues/closed/by-phase
mkdir -p scripts
mkdir -p .github/workflows
```

**Verify:** `find docs -type d` shows all folders.

### Step 2: Create Documentation Navigation Hub

**File: `docs/README.md`**

Use this template, replacing `{{PROJECT_NAME}}` and adapting roadmap section:

```markdown
# {{PROJECT_NAME}} — Documentation

> **Vision:** [One-line product vision — ask user]

---

## 🎯 Current Focus

**🚀 [Current phase name]**

- 📖 Read: [`current/phase-X-detailed.md`](current/phase-X-detailed.md)
- 📋 Issues: [`current/phase-X-issues.md`](current/phase-X-issues.md)

---

## 📚 Navigation

### Strategy
- 📜 [Product Strategy](current/strategy.md)
- 📝 [Migration Notes (if any)](archive/MIGRATION_NOTES.md)

### Implementation Guides

| Phase | Status | Detailed Doc | Issues |
|-------|--------|--------------|--------|
| [Phase 1] | [✅/⏳/📋] | [link] | [link] |

### Issues
- [Active Issues](issues/active/INDEX.md)
- [Closed Issues](issues/closed/INDEX.md)

### Archive
- Historical docs from past pivots

---

## 🗂️ Folder Structure

(Same as below — copy from this template)

---

## 🛠️ How to Use

### Starting development on a phase:
1. Read `current/strategy.md` — vision + positioning
2. Read `current/phase-X-detailed.md` — implementation guide
3. Open `current/phase-X-issues.md` — pick issues
4. Trigger AI implementation per issue

### When a project pivots:
1. Create `archive/MIGRATION_NOTES_VX_VY.md`
2. Move outdated docs to `archive/vX-name/`
3. Update this README
4. **Don't delete old files** — preserve history
```

### Step 3: Create Issues Folder Structure

**File: `docs/issues/README.md`**

Copy this content **verbatim** (only replace `{{PROJECT_NAME}}` if referenced):

```markdown
# Issues Folder

> Synced representation of GitHub issues in markdown format.
> Used by AI tools (Claude Code, etc.) for implementation context.

## 🗂️ Structure

\```
docs/issues/
├── README.md           ← You are here
├── active/             ← Currently open issues
│   ├── INDEX.md        ← Auto-generated table
│   └── issue-X.md      ← One file per open issue
└── closed/             ← Archived (completed) issues
    ├── INDEX.md        ← Auto-generated table (by phase)
    └── by-phase/
        ├── phase-1/
        ├── phase-2/
        └── unknown/    ← Issues without phase label
\```

## 🔄 How Files Get Here

1. Master file (e.g., `current/phase-X-issues.md`)
2. AI agent generates individual issue drafts
3. User reviews + approves
4. Agent pushes to GitHub → issue #N created
5. GitHub Action creates `active/issue-N.md`
6. Trigger AI to implement
7. PR merges → issue closes
8. GitHub Action moves file to `closed/by-phase/phase-X/`

## 📄 File Format

Each issue file has YAML frontmatter:

\```markdown
---
issue_number: 42
title: "Build feature X"
phase: phase-1
status: active             # or 'closed'
labels: [phase-1, backend]
github_url: https://github.com/.../issues/42
updated_at: 2026-04-25T10:30:00Z
closed_at: 2026-04-28T15:00:00Z  # only for closed
---

# Issue title

[Issue content from GitHub]
\```

## ⚙️ Conventions

- **Source of truth:** GitHub issue (markdown mirrors state)
- **Auto-managed:** Don't manually edit files — edit GitHub issue
- **Phase-organized:** Closed issues sorted by phase for findability
- **AI context:** Individual files give focused context per issue

## 🤖 AI Agent Instructions

When triggered to implement issue #X:

1. Read `docs/issues/active/issue-X.md` (primary context)
2. Read `docs/current/phase-Y-detailed.md` (architecture, Y matches phase)
3. Read `CLAUDE.md` if exists (technical spec)
4. Reference related closed issues if pattern relevant
5. Create PR with "closes #X"
6. GitHub Action handles file move on close
```

### Step 4: Install Migration Script (only if existing project has issues)

**Skip this step for greenfield projects.**

**File: `scripts/migrate-issues.sh`**

Create with executable permission:

```bash
chmod +x scripts/migrate-issues.sh
```

Content (full script template — adjust `PHASE_RANGES` per project):

```bash
#!/bin/bash
# migrate-issues.sh — Reorganize existing docs/issues/ into active/closed structure
# Usage: ./scripts/migrate-issues.sh [--dry-run]

set -euo pipefail

ISSUES_DIR="docs/issues"
DRY_RUN=false

# CUSTOMIZE: Phase ranges based on project's actual issue numbers
# Edit before running. Use `gh issue list --state closed --label phase-X` to determine ranges.
declare -A PHASE_RANGES=(
  ["phase-1"]="1-25"
  ["phase-2"]="26-45"
  # Add more as needed
)

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

execute() {
  if $DRY_RUN; then echo "[DRY] $*"; else eval "$*"; fi
}

detect_phase() {
  local num=$1
  # Try gh CLI first
  if command -v gh &> /dev/null; then
    local phase
    phase=$(gh issue view "$num" --json labels --jq '.labels[].name' 2>/dev/null | grep -oE 'phase-[0-9a-z]+' | head -1)
    [[ -n "$phase" ]] && echo "$phase" && return
  fi
  # Fallback to ranges
  for p in "${!PHASE_RANGES[@]}"; do
    local range="${PHASE_RANGES[$p]}"
    local start="${range%-*}" end="${range#*-}"
    if (( num >= start && num <= end )); then echo "$p"; return; fi
  done
  echo "unknown"
}

[ -d ".git" ] || { echo "Not in repo root"; exit 1; }
[ -d "$ISSUES_DIR" ] || { echo "$ISSUES_DIR not found"; exit 1; }

execute "mkdir -p $ISSUES_DIR/active $ISSUES_DIR/closed/by-phase"

moved=0
for file in "$ISSUES_DIR"/issue-*.md; do
  [ -e "$file" ] || continue
  num=$(basename "$file" | grep -oE '[0-9]+' | head -1)
  [ -z "$num" ] && continue
  
  phase=$(detect_phase "$num")
  target="$ISSUES_DIR/closed/by-phase/$phase"
  execute "mkdir -p $target"
  execute "git mv $file $target/"
  echo "  Moved #$num → $phase"
  ((moved++))
done

echo "Migrated $moved issues"
$DRY_RUN && echo "(DRY RUN — no actual changes)"
```

**Run:** `./scripts/migrate-issues.sh --dry-run` first, review, then run without flag.

### Step 5: Install Index Generator

**File: `scripts/generate-issues-index.py`**

Requirements: `pip install pyyaml`

Full script — copy verbatim:

```python
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
                print(f"✓ {out}")

if __name__ == "__main__": main()
```

### Step 6: Install GitHub Action (skip if no GitHub automation needed)

**File: `.github/workflows/issue-lifecycle.yml`**

Copy verbatim:

```yaml
name: Issue Lifecycle Sync

on:
  issues:
    types: [opened, edited, closed, reopened, labeled, unlabeled]

permissions:
  contents: write
  issues: read

jobs:
  sync:
    runs-on: ubuntu-latest
    if: ${{ !contains(github.event.issue.labels.*.name, 'no-sync') }}
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      
      - run: pip install pyyaml
      
      - name: Detect phase
        id: phase
        env: { LABELS: ${{ toJson(github.event.issue.labels) }} }
        run: |
          P=$(echo "$LABELS" | jq -r '.[].name' | grep -oE 'phase-[0-9a-z]+' | head -1 || echo "unknown")
          echo "phase=$P" >> $GITHUB_OUTPUT
      
      - name: Sync open/edited
        if: contains(fromJson('["opened","edited","reopened"]'), github.event.action)
        env:
          NUM: ${{ github.event.issue.number }}
          TITLE: ${{ github.event.issue.title }}
          BODY: ${{ github.event.issue.body }}
          PHASE: ${{ steps.phase.outputs.phase }}
          LABELS: ${{ toJson(github.event.issue.labels) }}
          URL: ${{ github.event.issue.html_url }}
        run: |
          mkdir -p docs/issues/active
          TARGET="docs/issues/active/issue-${NUM}.md"
          
          # If reopening, move from closed back
          CLOSED=$(find docs/issues/closed -name "issue-${NUM}.md" 2>/dev/null | head -1)
          [ -n "$CLOSED" ] && git mv "$CLOSED" "$TARGET"
          
          LABELS_YAML=$(echo "$LABELS" | jq -c '[.[].name]')
          
          cat > "$TARGET" <<EOF
          ---
          issue_number: ${NUM}
          title: "${TITLE}"
          phase: ${PHASE}
          status: active
          labels: ${LABELS_YAML}
          github_url: ${URL}
          updated_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
          ---
          
          # ${TITLE}
          
          > GitHub: [#${NUM}](${URL}) • Phase: ${PHASE}
          
          ${BODY}
          EOF
      
      - name: Archive closed
        if: github.event.action == 'closed'
        env:
          NUM: ${{ github.event.issue.number }}
          PHASE: ${{ steps.phase.outputs.phase }}
          CLOSED_AT: ${{ github.event.issue.closed_at }}
        run: |
          ACTIVE="docs/issues/active/issue-${NUM}.md"
          [ -f "$ACTIVE" ] || ACTIVE=$(find docs/issues -name "issue-${NUM}.md" | head -1)
          [ -z "$ACTIVE" ] && exit 0
          
          TARGET_DIR="docs/issues/closed/by-phase/${PHASE}"
          mkdir -p "$TARGET_DIR"
          git mv "$ACTIVE" "$TARGET_DIR/"
          
          TARGET="$TARGET_DIR/issue-${NUM}.md"
          sed -i "s/^status: active$/status: closed\nclosed_at: ${CLOSED_AT}/" "$TARGET"
      
      - name: Regenerate INDEX
        run: |
          [ -f scripts/generate-issues-index.py ] && python scripts/generate-issues-index.py || true
      
      - name: Commit
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git diff --quiet && git diff --staged --quiet && exit 0
          git add docs/issues/
          git commit -m "docs(issues): sync #${{ github.event.issue.number }} (${{ github.event.action }})"
          git push
```

### Step 7: Initial INDEX Generation

```bash
pip install pyyaml
python scripts/generate-issues-index.py
```

Verify: `docs/issues/active/INDEX.md` and `docs/issues/closed/INDEX.md` exist.

### Step 8: Update Repo Root README.md

Add this section to root `README.md` (don't replace existing content):

```markdown
## Documentation

- 🎯 [Product Strategy](docs/current/strategy.md)
- 📋 [Current Phase](docs/current/phase-X-detailed.md)
- 📚 [All Docs](docs/README.md)
- 🐛 [Issues](docs/issues/README.md)
```

### Step 9: Verify Workflow End-to-End

If GitHub Action installed, test with a dummy issue:

1. Create test issue on GitHub with `phase-1` label
2. Wait ~30 seconds for Action to run
3. Verify: `docs/issues/active/issue-N.md` created
4. Close test issue
5. Verify: file moved to `docs/issues/closed/by-phase/phase-1/`
6. Verify: `INDEX.md` files updated
7. Delete test issue

### Step 10: Commit Everything

```bash
git add docs/ scripts/ .github/ README.md
git commit -m "chore: install standardized docs + issue workflow

- Add docs/ folder structure (current/, archive/, issues/)
- Add issue lifecycle automation (GitHub Action)
- Add migration + index generation scripts
- Add navigation README files

See docs/README.md for full structure."
```

---

## What This Setup Does NOT Include

These are project-specific and must be created separately:

- ❌ `docs/current/strategy.md` — product vision (user writes per project)
- ❌ `docs/current/phase-X-detailed.md` — implementation guides (user writes per phase)
- ❌ `docs/current/phase-X-issues.md` — master issue lists (user writes per phase)
- ❌ `CLAUDE.md` — technical spec (user writes per project)

These are **outputs** of strategic thinking, not template-able. The workflow just provides the **container**.

---

## Naming Conventions (Important for Consistency)

Always use these patterns across all projects:

| Type | Pattern | Example |
|------|---------|---------|
| Phase folder | `phase-{number}{optional-letter}` | `phase-1`, `phase-3a`, `phase-3b` |
| Phase doc | `phase-X-detailed.md` | `phase-3a-detailed.md` |
| Issues master | `phase-X-issues.md` | `phase-3a-issues.md` |
| Issue file | `issue-{github-number}.md` | `issue-42.md` |
| Migration note | `MIGRATION_NOTES_VX_VY.md` | `MIGRATION_NOTES_V1_V2.md` |
| Archive folder | `vX-{description}` | `v1-finance-assistant` |

---

## Common Pitfalls to Avoid

1. **Don't manually edit `docs/issues/active/issue-X.md`** — edits will be overwritten by GitHub Action. Edit the GitHub issue instead.

2. **Don't delete files when pivoting** — move to `archive/` instead. Git history alone is not enough for navigation.

3. **Don't skip `git mv`** — using `mv` loses file history.

4. **Don't mix issue tracking systems** — pick GitHub issues XOR another tool. This template assumes GitHub.

5. **Don't customize phase ranges in migrate script blindly** — verify with `gh issue list --label phase-X` first.

6. **Don't enable workflow without testing** — first test with `[no-sync]` label to verify, then remove.

---

## Customization Points

When adapting to a new project, these are the only places that need changes:

1. **`docs/README.md`** — project name, vision, phase list
2. **`docs/issues/README.md`** — only if AI agent instructions differ
3. **`scripts/migrate-issues.sh`** — `PHASE_RANGES` dictionary
4. **Root `README.md`** — add Documentation section

Everything else is generic and works across projects.

---

## Success Criteria

After implementation, the user should be able to:

- [ ] Navigate to `docs/README.md` and understand project structure
- [ ] See current phase status at a glance
- [ ] Create a GitHub issue and have it auto-mirror to `docs/issues/active/`
- [ ] Close a GitHub issue and have it auto-archive to `docs/issues/closed/by-phase/{phase}/`
- [ ] See updated `INDEX.md` after each issue lifecycle event
- [ ] Trigger AI implementation by referencing `docs/issues/active/issue-N.md`

If any of these fail, debug before declaring done.

---

## Implementation Order Rationale

The order matters. If you implement out of sequence, things break:

1. **Folder structure first** — everything else depends on it
2. **Navigation READMEs second** — describe intent before automation
3. **Migration script third** (if needed) — clean up before adding automation
4. **Index generator fourth** — needed for verifying migration
5. **GitHub Action fifth** — only after structure is correct
6. **Test sixth** — verify before committing
7. **Commit last** — single atomic commit for the whole setup

---

## Final Notes for AI Implementer

- This file is your spec. Refer back when in doubt.
- When user provides project context, fill in `{{PLACEHOLDERS}}`.
- If user has existing structure conflicting with this template, **ask before overwriting**.
- The goal is to clone the working pattern, not innovate. Resist creative deviation.
- After implementation, summarize what was created and any decisions made.
