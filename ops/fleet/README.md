# ContentCreator — Dev/Production operations

Production runs on this Mac Mini; the live bot/schedules and content DB are real user workloads. There is no active VPS Production. Existing uncommitted code and OAuth files must be preserved and reconciled before any code overlay; never reset/pull the live folder to make it clean.

## Dev preparation

Stage only the approved SHA in a new isolated directory, without `.env`, OAuth/client-secret/token files, DB, logs, media outputs or live launchd definitions. Use a fresh Dev DB initialized by the existing application migrations. `python ops/fleet/verify_dev.py <dev-db>` verifies integrity and required application tables **read-only**. It intentionally reports user_path/services/publishing_guard as incomplete: schema success alone is not a usable Dev release.

Register a separate Dev workspace, data/cache/output roots and service labels. Start no live bot, scheduler, uploader or publishing/approval endpoint. Real credentials must not be inherited from the shell or loaded from a parent/live `.env`; use synthetic data and local preview first. A Telegram user-path test needs a separately provisioned Dev bot/channel, not the real publishing bot. Any provider-backed generation requires its own explicit authorized credentials and cost budget.

Readiness checks: artifact, isolation, database, services, user_path and publishing_guard. Validate an actual preview corresponding to the changed feature, show where the PO can inspect it, and demonstrate publishing is disabled before inviting Dev acceptance. Keep dev_batch off until the isolated runtime and these checks have been observed.

## Production activation and rollback

After Dev acceptance and separate Production approval, record all affected `com.ai5phut.*` labels and current runtime fingerprints. Pause the relevant publishing/queue/bot jobs, confirm no active write/upload, create a consistent SQLite backup and a code backup, and apply only approved code. Preserve the DB, queue, OAuth/token files, media and launchd scheduling definitions. Restart only affected registered labels, verify the running code hashes, logs, actual bot command/preview path and resumption of the intended schedules. Do not publish a real test video to prove deployment without explicit approval.

If a check fails, do not announce success. Inspect the code journal and restore approved code while services are quiescent, then recheck the user path. Preserve all content/queue writes and publishing receipts; a code rollback must not reset successful uploads or cause duplicate posts.

## Delivery contract

PM owns requirements/issues and PO communication; the coding worker proposes code and stops at a PR. The PO merges. DevOps operates deployments only after a separate version-bound approval. A merge, passing CI, generated plan or health-only check is not deployment or owner acceptance.

The shared controller stages reviewed code from Git objects by full SHA, with a manifest digest; it never pulls/reset the live checkout. Host paths, service identifiers, baseline observations and credentials stay in private host bindings outside Git. `ops/fleet/**` is a maintenance path: normal coding workers cannot modify this adapter to grant themselves execution.

Before activation, DevOps must record the observed runtime files, review uncommitted/local changes, verify candidate CI, obtain exact scope/version/environment approval, establish quiescence and create a recoverable backup. Apply selected code only, restart only registered affected services, check the exact running version and all project readiness checks, then persist a deployment receipt. PM can invite PO acceptance only after those checks pass. Preserve unknown/failed states and never replay a deployment intent blindly.

Code rollback uses the private file journal and verifies no newer owner edits would be overwritten. Database rollback is a separate data decision: never restore an older DB after new writes without reconciliation. Keep deployment history, signing keys, approval references and recovery evidence.
