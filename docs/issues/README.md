# Issues Folder

> Synced representation of GitHub issues in markdown format.
> Used by AI tools (Claude Code, etc.) for implementation context.

## 🗂️ Structure

```
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
```

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

```markdown
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
```

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
