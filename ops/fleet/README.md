# ContentCreator — Dev/Production operations

Production runs on this Mac Mini; the live bot/schedules and content DB are real user workloads. There is no active VPS Production. Existing uncommitted code and OAuth files must be preserved and reconciled before any code overlay; never reset/pull the live folder to make it clean.

## Dev preparation

Stage only the approved SHA in a new isolated directory, without `.env`, OAuth/client-secret/token files, DB, logs, media outputs or live launchd definitions. Use a fresh Dev DB initialized by the existing application migrations. `python ops/fleet/verify_dev.py <dev-db>` verifies integrity, exact candidate migration versions, required column definitions and indexes **read-only**. It builds a disposable reference DB using the real migration runner with a stub config, without loading dotenv or provider credentials. It intentionally reports user_path/services/publishing_guard as incomplete: schema success alone is not a usable Dev release.

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

## Interactive Dev approval (no publishing)

`prepare_dev.py <SHA-pinned-artifact> --state <new-private-dir> --output <new-compose.json>` prepares a dedicated `fleet-content-dev` service. Start only that compose project after the version/environment approval. The existing Streamlit UI is available on `http://127.0.0.1:18866`; it uses actual migrations and review-service state transitions with synthetic videos. The Dev button is **Duyệt thử**. It never posts to YouTube/TikTok/Telegram. No new Telegram bot is required: Ốp Dev remains the operations contact, PM sends the acceptance link.

The container mounts the staged source read-only and its own marked state directory, has an internal Docker network, no credentials, and no host service/socket mounts. Explicit development mode skips dotenv and moves DB/output/queue/OAuth paths to that separate state root. Publishing entry points and scheduler ticks fail before side effects. Do not attach Production credentials or use this fixture service for real publishing.

Readiness requires exact manifest/file hashes, real schema verification, a fresh UI approve/reject against synthetic rows, service health, network/mount inspection and publisher barrier tests. A health response alone is insufficient. `test_dev_runtime.py` exercises the UI through Streamlit AppTest, not a mocked review service. No AI API is used. Leave owner acceptance false until the PO tries the UI.

Updating Dev: stop only `fleet-content-dev`, back up the private Dev SQLite DB, stage the approved full SHA, prepare a new compose file pointing to that artifact and the existing marked Dev state, rebuild/restart only this project, rerun the checks and record a version-bound receipt. Never pull/reset the live ContentCreator checkout. Recovery restores the previous compose/artifact and preserves any newer state; database restore is a separate decision.
