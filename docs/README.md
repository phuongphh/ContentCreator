# ContentCreator — Documentation

> **Vision:** Giúp người Việt đi làm văn phòng (22–35 tuổi) hiểu và dùng AI trong 5 phút mỗi ngày, qua kênh YouTube/TikTok với chi phí vận hành ~$4/tháng.

---

## 🎯 Current Focus

**🚀 Pipeline tự động hoá content (collection → scoring → analysis → publish)**

- 📖 Read: [`current/strategy.md`](current/strategy.md)
- 🛠️ Tech spec: [`../CLAUDE.md`](../CLAUDE.md)
- 📋 Master issue list: _(create `current/phase-X-issues.md` per phase)_

---

## 📚 Navigation

### Strategy
- 📜 [Product Strategy](current/strategy.md)
- 📝 [Migration Notes (if any)](archive/)

### Implementation Guides

| Phase | Status | Detailed Doc | Issues |
|-------|--------|--------------|--------|
| _(none yet)_ | 📋 | _add `current/phase-1-detailed.md`_ | _add `current/phase-1-issues.md`_ |

### Issues
- [Active Issues](issues/active/INDEX.md)
- [Closed Issues](issues/closed/INDEX.md)
- [Issues README](issues/README.md)

### Archive
- Historical docs from past pivots → `archive/`

---

## 🗂️ Folder Structure

```
docs/
├── README.md              ← You are here
├── current/               ← Active strategy + phase docs
│   ├── strategy.md
│   ├── phase-X-detailed.md
│   └── phase-X-issues.md
├── archive/               ← Outdated docs (preserved, not deleted)
│   └── vX-{description}/
└── issues/                ← Bidirectional sync with GitHub issues
    ├── README.md
    ├── active/
    │   ├── INDEX.md       (auto-generated)
    │   └── issue-N.md
    └── closed/
        ├── INDEX.md       (auto-generated)
        └── by-phase/
            ├── phase-1/
            ├── phase-2/
            └── unknown/   (issues without phase label)
```

---

## 🛠️ How to Use

### Starting development on a phase
1. Read `current/strategy.md` — vision + positioning
2. Read `current/phase-X-detailed.md` — implementation guide
3. Open `current/phase-X-issues.md` — pick issues
4. Trigger AI implementation per issue

### When the project pivots
1. Create `archive/MIGRATION_NOTES_VX_VY.md`
2. Move outdated docs to `archive/vX-name/`
3. Update this README
4. **Don't delete old files** — preserve history

### Issue lifecycle (automated)
1. Create issue on GitHub (add `phase-X` label if applicable)
2. GitHub Action mirrors it to `docs/issues/active/issue-N.md`
3. AI implements → PR merges → issue closes
4. Action moves the file to `docs/issues/closed/by-phase/phase-X/`
5. `INDEX.md` is regenerated automatically
