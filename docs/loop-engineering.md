# Local Loop Engineering

PM manages requirements, task scope and acceptance. The local DevOps agent operates
the shared `phuongphh/fleet-engineering` runner with this project's reviewed profile.
Claude is the persisted default; an owner can select Codex for one task or ask PM
to change the default for future tasks. Product code and tests stay in this repo.

The shared controller owns `claude/local-issue-*` and `codex/local-issue-*` PRs.
Legacy auto-PR excludes these branches. Coding stops at a PR with exact-head CI
evidence; merge does not grant deployment approval. Tests use disposable CI data
and no production credentials. Deployment and user acceptance are separate steps.

The authoritative operations guide is maintained in the private platform repository:
https://github.com/phuongphh/fleet-engineering/blob/main/docs/OPERATIONS-VI.md

ContentCreator is a personal product. Both Dev and Production are on the owner's
Mac Mini, operated by local DevOps with separate approvals/data. The obsolete
main-to-VPS workflow is removed; merging a PR does not publish videos or restart
production services. The CI unit suite does not certify live publishing or
complete video rendering; those need their own acceptance evidence.
