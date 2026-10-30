# Local Loop Engineering

PM manages requirements, task scope and acceptance. The local DevOps agent operates
the shared `phuongphh/fleet-engineering` runner with this project's reviewed profile.
Claude is the persisted default; an owner can select Codex for one task or ask PM
to change the default for future tasks. Product code and tests stay in this repo.

The shared controller owns `claude/local-issue-*` and `codex/local-issue-*` PRs.
Legacy auto-PR excludes these branches. Coding stops at a PR with exact-head CI
evidence; merge does not grant deployment approval. Tests use disposable CI data
and no production credentials. Deployment and user acceptance are separate steps.

Authoritative operations guide:
https://github.com/phuongphh/fleet-engineering/blob/main/docs/OPERATIONS-VI.md

Reviewed project profile (defines CI, environment and protected-path policy):
https://github.com/phuongphh/fleet-engineering/blob/main/projects/ContentCreator.json

Profile policy governs CI environment and
protected-path restrictions; task write_paths and context_paths come from the
approved prepared task, not the profile. `dev_paths` in the profile describes
deployment impact scope, not coding write scope.

## Platform boundaries

ContentCreator is a personal product. Dev and Production both run on the owner's
Mac Mini with separate activation, data directories and approvals. The obsolete
main-to-VPS workflow is removed. Merging a PR does not publish videos, trigger
provider execution or restart production services. The runner processes one task
at a time (global slot); multi-task queue and Pro transport carry known limitations.

## CI gates (exact PR head required)

Required named jobs: **`unit`** and **`review`**. Both must pass on the exact PR
head commit. Do not invent additional job names. CI passing confirms code-level
checks only — it is not Dev acceptance, not confirmed video rendering and not
Production approval.

## Project-specific acceptance checklist

PM collects the following evidence before requesting owner PR review:

- [ ] `unit` job green on exact PR head (GitHub Actions link)
- [ ] `review` job green on exact PR head (GitHub Actions link)
- [ ] Diff scoped only to `write_paths` from the approved task (no profile, workflow or runtime files changed)
- [ ] Summary of what the change does and which acceptance criteria it addresses

Owner next actions at PR review:
1. Read PM’s short outcome and evidence summary; confirm it matches your request.
2. Approve and merge if satisfied — this is a coding merge only.
3. For Dev acceptance: approve the scope PM proposes. PM coordinates DevOps,
   then offers acceptance only after the agreed version, services and user path are verified.
   Test with agreed acceptance data and report pass/fail to PM.
4. For Production: follow the full promotion flow with independent approval; PM coordinates DevOps and proactively requests required approvals — the PO does not contact DevOps directly.

Complete video rendering and real publication require their own acceptance evidence
collected after Dev activation. No automatic deploy, ingest or Gateway change
occurs as part of any documentation or coding PR.
