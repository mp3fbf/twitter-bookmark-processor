# Bookmark automation v2

Provider-neutral automation for **Twitter bookmarks only**. Likes have zero effect.
This package is isolated from the legacy `src/` pipeline and never imports its
Anthropic, OpenAI, or Gemini SDK paths.

Nothing in `systemd/` is installed or enabled by this change. No live Twitter,
Telegram, vault, Claude, or Codex request is made by the test suite.

## Domain flow

1. A mandatory one-time bootstrap imports
   `/usr/bin/bird bookmarks --all --json` as historical state without creating
   notification, video, or semantic jobs. Only a successful bootstrap creates
   the sidecar's `.bootstrap-complete` marker.
2. A one-time coverage scan reconciles existing Twitter Source notes with the
   historical bookmarks before periodic catch-up is allowed. It fails closed if
   any Markdown file lacks a valid `bookmark_id` in YAML frontmatter.
3. After bootstrap, `/usr/bin/bird bookmarks -n 20 --json` supplies the newest
   raw bookmarks. A daily full reconciliation recovers anything missed by the
   incremental poll through the same normal ingestion path.
4. Normal ingestion stores one current bookmark record plus jobs keyed by
   `(bookmark_id, task_kind, input_revision)`. Capture effects and semantic
   revisions are deliberately different: a post-bootstrap bookmark can notify
   only once and can own only one video-delivery job, while meaningful content
   revisions may queue fresh semantic work.
5. A newly captured post-bootstrap bookmark atomically queues:
   - `notify` immediately, once for that `bookmark_id`;
   - one `deliver_video` job immediately when `hasVideo=true` (the CLI derives
     it from Bird `media[].type=video`). Until that job succeeds, a richer Bird
     revision can refresh its pending execution snapshot and replace an
     exhausted retry budget without creating a second delivery identity;
   - `fetch_article` for URL/article content;
   - `quick` immediately and independently;
   - `recall_context` independently, using quick output when available and a
     bounded mechanical text/title query otherwise;
   - `deep` for articles after a 15-minute grace window.
   Later content revisions queue only the applicable semantic stages. A
   bootstrap-created historical bookmark can never gain immediate notification
   or video effects merely because reconciliation later changes its payload.
6. `deep` sees the raw bookmark and the receipts currently available from
   capture, quick sensemaking, and local recall. Missing/failed stages remain
   explicit evidence states; the provider cannot fetch or read the workspace.
7. `deep` success atomically queues Source-note materialization and the richer
   Telegram follow-up. The daily `aggregate` freezes only bookmark revisions not
   covered by a prior digest. The historical `backlog` advances in bounded
   per-bookmark slices and never retroactively sends an immediate notification
   or video. Jobs retain only logical profiles (`aggregate`, `deep`):
   provider/model selection lives in the shared subscription runner.

The Telegram question is about urgency, not permission to process:

- `act`: release and promote `deep` now;
- `keep`: retain the normal deep schedule;
- `defer`: create or resume the evidence chain and move deep work by the
  configured delay;
- `skip`: atomically cancel `quick`, `fetch_article`, `recall_context`, `deep`,
  and any pending Source-note/follow-up work, including a currently leased
  semantic job, while preserving the bookmark, notification, and automatic
  native-video delivery.

All decisions are append-only. Queue transitions, receipts, and Source-note
materialization are idempotent; nothing deletes the captured bookmark. Telegram
delivery is deliberately **at-least-once**: a process crash after Telegram
accepts a message/file but before the SQLite receipt commits can produce a
duplicate on retry. The receipt prevents repeats after a completed commit, not
inside that crash window.

For cancellable semantic work, `skip` and the worker serialize around a durable
effect-commit boundary. Reversible article fetch and local recall remain
cancellable while leased. Before the boundary, a Source write or Telegram send
is cancelled and the effect does not start. After it, the owner keeps its
fencing token even if the ordinary lease deadline passes, so an already-started
write/send can finish and persist its receipt; if it fails, the durable cancel
request prevents a retry after `skip`. Another worker never automatically
reclaims an `effect_committed` attempt. A process crash after that boundary
therefore leaves an operator-visible committed effect requiring audited
recovery rather than risking an automatic duplicate.

Automatic video delivery currently covers only native X media with an approved
`video.twimg.com` URL in Bird's payload. A YouTube, Vimeo, or other external
video link is not downloaded as a video by this version; it remains URL/article
material for semantic processing. Supporting another host requires a separate,
host-specific capture contract rather than relaxing the native-media allowlist.
Each original is published no-clobber at
`data/videos/<tweet-id>--<sha256>.mp4`; a new revision can never overwrite bytes
referenced by an older receipt. Files above Telegram's hosted Bot API limit are
transcoded to a separate delivery copy while the original remains unchanged.

Bootstrap is deliberately silent, not a discard. Historical revisions without
an accepted existing Source note remain eligible for gradual semantic
processing, five per daily run, while the near-real-time Telegram behavior
starts only with post-bootstrap deltas.

## Second Brain invariants

- Every bookmark is captured. Relevance ranks priority; it never filters a top-N.
- Capture, retrieval, sensemaking, and effects are separate stages.
- An article produces an auditable Source-note **draft** with provenance.
- Recurring concepts produce `promotion_candidates`; the provider never creates
  or overwrites a canonical page.
- Concepts may consolidate, episodes may archive, and nothing is deleted.
- Core code owns URL fetching, local recall, note paths, note writes, and
  Telegram effects. Prompts treat all source/context as untrusted data, cap each
  source string at 12,000 characters, and require prompt-injection signals.
- A detected prompt injection removes promotion candidates, marks the result as
  quarantined, and is surfaced in both the immutable Source note and Telegram;
  model HTML and remote-image Markdown are rendered inert in notes. The signal
  is sticky: a flag from quick triage or any member of an aggregate cannot be
  cleared by a later model stage.

## Sidecar and queue

The SQLite sidecar contains `bookmarks`, `jobs`, `attempts`, `receipts`,
`decisions`, coverage watermarks, and bootstrap metadata. It is operational
state, not another knowledge base.

Job states are `pending`, `leased`, `done`, `waiting_provider`, `cancelled`, and
`dead_letter`. Every claim receives a fencing token; expired, reclaimed, or
cancelled workers cannot commit a receipt or enqueue downstream work.
Authentication/quota absence becomes `waiting_provider` with backoff; repeated
ordinary failures enter `dead_letter`. There is no API-key or paid-API fallback.

Priority order starts with `act`, then immediate notification/delivery and new
bookmark analysis, then aggregate, then backlog.

## Offline commands

All examples below operate only on fixtures/local SQLite:

```bash
PYTHONPATH=. python3 -m bookmark_automation \
  --db /tmp/bookmark-automation.sqlite3 ingest \
  --kind bookmarks \
  --bootstrap --expected-minimum 1 \
  --input bookmark_automation/fixtures/bird-bookmarks.sample.json

PYTHONPATH=. python3 -m bookmark_automation \
  --db /tmp/bookmark-automation.sqlite3 gate

install -d -m 0700 /tmp/bookmark-notes
PYTHONPATH=. python3 -m bookmark_automation \
  --db /tmp/bookmark-automation.sqlite3 import-note-coverage \
  --notes-dir /tmp/bookmark-notes

printf '%s\n' \
  '{"event_id":"telegram:1","tweet_id":"1900000000000000999","action":"keep"}' \
  | PYTHONPATH=. python3 -m bookmark_automation \
      --db /tmp/bookmark-automation.sqlite3 import-decisions --input -

PYTHONPATH=. python3 -m bookmark_automation \
  --db /tmp/bookmark-automation.sqlite3 schedule \
  --task-kind aggregate --input-revision 2026-08-08 --batch-size 25

PYTHONPATH=. python3 -m bookmark_automation \
  --db /tmp/bookmark-automation.sqlite3 status

PYTHONPATH=. python3 -m bookmark_automation \
  --db /tmp/bookmark-automation.sqlite3 dead-letter-list
```

The ingest command accepts a single object, an array from non-paginated Bird,
or Bird's `{ "tweets": [...], "nextCursor": ... }` reconciliation envelope.
Raw Bird data must be labelled with `--kind bookmarks`; an explicit event-level
kind always wins, so `kind=likes` stays ignored.

`import-decisions` consumes append-only JSONL from the shared Telegram bridge.
It maps `tweet_id`/`event_id` to `bookmark_id`/`decision_id` and safely replays a
file because `event_id` is unique.

`schedule` defaults `input_revision` to today's date in
`America/Sao_Paulo`. Aggregate scheduling freezes only new or revised bookmark
inputs not already covered, in deterministic batches of at most 25 by default.
Each eligible bookmark revision occurs exactly once across those batches: there
is no top-N filter, and later ingestion cannot mutate a queued batch. With no
delta, scheduling creates zero aggregate jobs and sends no empty digest.
`--batch-size` changes the bound. Backlog scheduling applies that same bound to
historical current revisions still lacking deep processing. The staged daily
unit uses five; it queues the semantic capture/deep stages for those items but
never historical `notify` or `deliver_video` jobs.

The read-only `status` command reports queue, task, receipt, attempt, decision,
dead-letter, `waiting_provider`, and unresolved `committed_effects` counts.
`gate` runs SQLite `quick_check` and verifies that JSON markers, database UUID,
completion timestamps, absolute database path, accepted bootstrap count, and
positive expected minimum agree. The current bookmark count may grow but can
never fall below the accepted bootstrap cardinality.
`dead-letter-list` exposes only bounded job metadata and does not requeue. None
of these commands creates or migrates an absent database, calls Twitter,
Telegram, or an inference provider, or includes bookmark content.

## Subscription runner contract

The inference worker invokes only the shared router:

```text
PYTHONPATH=/workspace/_scripts/subscription-inference
python3 -m subscription_inference run --profile <quick|deep|vision|aggregate>
```

It sends one JSON object on stdin:

```json
{"prompt":"...","schema":{},"job_id":"42"}
```

and accepts one receipt with status `succeeded`, `waiting_provider`, or `failed`.
Jobs never name Claude, Codex, a provider, or a model. Prompts and output schemas
are versioned under `prompts/v1/` and `schemas/v1/`.

## Staged systemd operation

The repository stages seven service/timer pairs under `systemd/`; it does not
copy them to `/etc/systemd/system`, start them, or enable them. Their paths
assume the merged checkout lives at `/workspace/twitter-bookmark-processor`.

| Pair | Cadence | Responsibility |
|---|---:|---|
| `bookmark-automation-poll` | 60 seconds | Ingest the newest 20 bookmarks from Bird. |
| `bookmark-automation-reconcile` | daily, 03:30 BRT | Reconcile the full collection through normal ingestion and alert on genuinely missed deltas. |
| `bookmark-automation-effects` | 15 seconds | Notify, capture articles/context, download and send native video, write Source-note drafts, and send deep/aggregate messages. |
| `bookmark-automation-decisions` | 15 seconds | Replay the existing bridge's append-only callback JSONL idempotently. |
| `bookmark-automation-inference` | 60 seconds | Run provider-neutral `quick`, `deep`, and `aggregate` jobs through subscription CLIs. |
| `bookmark-automation-periodic` | daily, 07:10 BRT | Freeze incremental digest batches of 25 and advance at most five historical revisions. |
| `bookmark-automation-maintenance` | every 5 minutes | Alert new dead letters and ambiguous committed effects, checkpoint/measure SQLite WAL, prune completed videos after 30 days, and alert on low disk. |

The templates deliberately use the absolute command requested for near-real-time
polling:

```text
/usr/bin/bird bookmarks -n 20 --json
```

The poll timer targets 60 seconds. Daily reconciliation uses:

```text
/usr/bin/bird bookmarks --all --json
```

Every service is gated by the bootstrap marker at
`/workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3.bootstrap-complete`.
The periodic scheduler also requires
`/workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3.note-coverage-complete`;
the decisions importer additionally requires its bridge JSONL. Do not enable
timers until bootstrap, note coverage, and validation have succeeded. These
conditions are a defense-in-depth guard against accidental activation.

### Install preconditions

Complete and verify these steps before copying any unit:

1. Publish the shared runner at
   `/workspace/_scripts/subscription-inference` and keep the application checkout
   at `/workspace/twitter-bookmark-processor`.
2. Create the private runtime paths without putting credentials in Git:

   ```bash
   install -d -m 0700 /etc/bookmark-automation
   umask 077
   touch /etc/bookmark-automation/twitter.env
   touch /etc/bookmark-automation/telegram.env
   chmod 0600 /etc/bookmark-automation/twitter.env
   chmod 0600 /etc/bookmark-automation/telegram.env
   install -d -m 0700 /workspace/twitter-bookmark-processor/data/videos
   install -d -m 0700 /workspace/notes/Sources/twitter
   ```

   Populate `twitter.env` with only `AUTH_TOKEN` and `CT0`. Populate
   `telegram.env` with only `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. The bot
   must be the **same bot identity** consumed by
   `fasttrack-telegram-bridge.service`; using another valid bot sends buttons
   whose callbacks the bridge can never receive. Compare the Bot API `getMe`
   identity through the existing secret-management path without printing either
   token or environment file. The destination chat must be a personal Roberto
   channel, never a Prevent Senior/corporate destination, and must also be
   present in the bridge's chat allowlist.
3. Validate Bird authentication, then run the historical bootstrap exactly once
   before enabling timers:

   ```bash
   ( umask 077
     /usr/bin/bird bookmarks --all --json \
       | PYTHONPATH=/workspace/twitter-bookmark-processor \
         /usr/bin/python3 -m bookmark_automation \
         --db /workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3 \
         ingest --input - --kind bookmarks --bootstrap \
         --expected-minimum 1373
   )

   test -f /workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3.bootstrap-complete
   test "$(stat -c %a /workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3)" = 600
   ```

   Bootstrap writes bookmarks and the marker only after successful ingestion;
   it creates no jobs, Telegram messages, video downloads, or digest for the
   historical corpus. Never create the marker manually. It belongs to this exact
   sidecar database: a database replacement requires a deliberate restore or a
   new bootstrap before services may run. The bootstrap must fail closed on an
   empty collection and publish no marker. Before proceeding, compare its
   `accepted` count with the independently known collection size; zero or an
   unexpectedly small result is an X/Bird ingestion failure, even if the process
   exited normally. Keep every timer disabled and investigate rather than
   allowing a later reconcile to treat the historical collection as new.
4. Reconcile the 1,410 existing Markdown notes before allowing daily backlog
   work. The count is the state observed in
   `/workspace/notes/Sources/twitter` on 8 August 2026; review the command's
   reported `scanned`, `imported`, `duplicates`, and `unmatched` counts rather
   than assuming they stay fixed:

   ```bash
   PYTHONPATH=/workspace/twitter-bookmark-processor \
     /usr/bin/python3 -m bookmark_automation \
     --db /workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3 \
     import-note-coverage --notes-dir /workspace/notes/Sources/twitter

   test -f /workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3.note-coverage-complete
   ```

   The scan is fail-closed: every recursively discovered Markdown file must have
   `---` frontmatter containing a safe `bookmark_id`. Any malformed note aborts
   the import before coverage changes and before the marker is published, so the
   periodic unit remains gated. By default, a valid existing note suppresses
   backlog reprocessing. Add `--redo-thin` only after an explicit decision to
   reprocess legacy notes tagged `thin-content` or whose short knowledge section
   still contains a `t.co` link; those notes remain eligible for the five-per-day
   backlog.
5. Confirm the callback-router change is merged and deployed in the existing
   Telegram bridge. Its private
   `/etc/fasttrack-monitor/telegram-bridge.env` must contain the reviewed
   `TWITTER_CALLBACK_ALLOWED_CHAT_IDS`, `TWITTER_CALLBACK_ALLOWED_USER_IDS`, and
   `TWITTER_CALLBACK_EVENTS_FILE=/workspace/JP/data/twitter_bookmark_actions.jsonl`.
   Confirm that JSONL is append-only output from the bridge and every line has a
   stable unique `event_id`, `tweet_id`, and one action (`act`, `keep`, `defer`,
   or `skip`). The decisions service never calls `getUpdates`; the existing
   bridge remains the sole Telegram update consumer. Until the JSONL exists,
   systemd skips the importer because of `ConditionPathExists`.
6. Validate `/usr/bin/bird bookmarks -n 20 --json`, the Telegram chat/user
   allowlist, the offline fixture, subscription authentication, and all staged
   units. Do not run a live inference or send a Telegram canary implicitly.

The effects service is the only pair that reads `telegram.env`; it can write
only the sidecar data/video tree, Source-note directory, and its private `/tmp`.
The inference service explicitly points `CODEX_HOME` at
`/workspace/.mcp-tools/codex` and `CLAUDE_CONFIG_DIR` at `/root/.claude`, so a
provider can be enabled or disabled in the runner configuration without editing
the bookmark units. All services unset paid-API credentials, 1Password service
tokens, and runner-command overrides that they do not need. Provider CLIs
receive a second allowlisted, per-job environment from the shared runner; only
the minimal local subscription credential is copied into that isolated home.

Inference and deterministic effects intentionally drain one job per activation.
Inference has a 19-minute systemd timeout: three minutes beyond the 960-second
outer runner timeout and one minute before its 20-minute lease expires. Effects
has 15 minutes for the bounded 120-second download plus a possible 600-second
ffmpeg transcode and delivery overhead, leaving five minutes before its lease
expires.
The incremental Bird poll has a two-minute timeout. Full reconciliation gets
30 minutes because paginating the complete bookmark corpus can legitimately
exceed systemd's usual short oneshot timeout.

### Pre-enable validation

Run these read-only/offline checks from the merged checkout:

```bash
PYTHONPATH=/workspace/twitter-bookmark-processor \
  /usr/bin/python3 -m bookmark_automation \
  --db /workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3 status

CODEX_HOME=/workspace/.mcp-tools/codex \
PYTHONPATH=/workspace/_scripts/subscription-inference \
  /usr/bin/python3 -m subscription_inference doctor

systemd-analyze verify \
  /workspace/twitter-bookmark-processor/bookmark_automation/systemd/*.service \
  /workspace/twitter-bookmark-processor/bookmark_automation/systemd/*.timer

PYTHONPATH=/workspace/twitter-bookmark-processor \
  /usr/bin/python3 -m bookmark_automation \
  --db /workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3 \
  operational-gate \
  --path /workspace/twitter-bookmark-processor/data \
  --min-free-bytes 1073741824
```

Before activation, the `status` receipt must report both
`"bootstrap_completed": true` and `"note_coverage_completed": true`, while
`gate --require-note-coverage` must report `"integrity_valid": true`,
`"bootstrap_count_valid": true`, and `"valid": true`.

`doctor` performs local authentication probes only. With the current runner
configuration, Codex is enabled and Claude is disabled; changing subscriptions
is a routing-config change, not a bookmark-worker or systemd change. If no
enabled subscription is authenticated, jobs remain `waiting_provider`; there is
no paid-API fallback.

Only after the checks pass and the operator explicitly approves activation,
copy the reviewed files, reload systemd, and enable the selected timers. The
repository intentionally does none of this:

```bash
install -m 0644 bookmark_automation/systemd/*.service /etc/systemd/system/
install -m 0644 bookmark_automation/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now \
  bookmark-automation-poll.timer \
  bookmark-automation-reconcile.timer \
  bookmark-automation-effects.timer \
  bookmark-automation-decisions.timer \
  bookmark-automation-inference.timer \
  bookmark-automation-periodic.timer \
  bookmark-automation-maintenance.timer
```

After an approved activation, inspect health without revealing stored content:

```bash
systemctl list-timers 'bookmark-automation-*'
systemctl --no-pager --full status bookmark-automation-effects.timer
journalctl -u 'bookmark-automation-*' --since today --no-pager
```

### Dead letters and recovery

`dead_letter` is durable quarantine, not successful processing. An aggregate
revision is watermarked when its frozen job is queued, so deleting that job or
its coverage row by hand can either lose it from future digests or duplicate it.
Never repair this state with ad-hoc SQLite updates.

`bookmark-automation-maintenance` sends an operator-visible Telegram alert once
per dead-letter attempt generation. Inspect and requeue without changing the
frozen input, attempt history, or aggregate coverage:

```bash
python3 -m bookmark_automation --db data/bookmark-automation.sqlite3 \
  dead-letter-list
python3 -m bookmark_automation --db data/bookmark-automation.sqlite3 \
  dead-letter-requeue --job-id 42 --reason "provider configuration repaired"
```

The requeue is idempotent. `waiting_provider` remains a separate non-terminal
state and is not accepted by the DLQ command.

An unresolved `committed_effect` is alerted separately and never requeued by a
worker. The operator must record one audited resolution:

```bash
python3 -m bookmark_automation --db data/bookmark-automation.sqlite3 \
  committed-effect-list
python3 -m bookmark_automation --db data/bookmark-automation.sqlite3 \
  committed-effect-resolve --job-id 43 --resolution confirmed \
  --reason "Telegram showed the message"
# Only with positive evidence that the effect did not occur:
python3 -m bookmark_automation --db data/bookmark-automation.sqlite3 \
  committed-effect-resolve --job-id 43 --resolution not-delivered \
  --reason "Bot API request never left the host"
```

Maintenance checkpoints and reports SQLite/WAL sizes, prunes only expired files
whose `deliver_video` job is already `done`, and alerts once per low-disk
incident. Every content/inference worker has an independent 1 GiB free-space
`ExecCondition`; maintenance deliberately remains runnable so it can prune and
alert while workers are blocked.

### Rollback

Rollback stops new work; it cannot retract a Telegram delivery or a Source note
that was already committed. Disable all timers first, then stop any active
oneshot workers:

```bash
systemctl disable --now \
  bookmark-automation-poll.timer \
  bookmark-automation-reconcile.timer \
  bookmark-automation-effects.timer \
  bookmark-automation-decisions.timer \
  bookmark-automation-inference.timer \
  bookmark-automation-periodic.timer \
  bookmark-automation-maintenance.timer

systemctl stop \
  bookmark-automation-poll.service \
  bookmark-automation-reconcile.service \
  bookmark-automation-effects.service \
  bookmark-automation-decisions.service \
  bookmark-automation-inference.service \
  bookmark-automation-periodic.service \
  bookmark-automation-maintenance.service
```

Preserve the SQLite database together with its `-wal`/`-shm` files and both
markers, plus `data/videos/`, the Source notes, and the append-only callback
JSONL. Take a SQLite-consistent backup after workers stop; do not delete or mix
markers from another sidecar instance. A code rollback may leave the reviewed
units installed but disabled. Re-enable only after restoring a mutually
compatible application/runner/bridge set and repeating every pre-enable gate.
Callbacks accumulated in the JSONL while disabled can then be replayed
idempotently.

### Go-live gates that remain operational

The code and units are staged only; production activation requires explicit
operator approval and remains blocked until all of these are true:

- the shared subscription runner and the Telegram callback router are merged,
  published, configured, and contract-tested with this checkout;
- bootstrap rejects an empty payload, its accepted count is plausible, the
  SQLite integrity/cardinality and sidecar/marker identity gates pass, and no
  historical notification/video job exists;
- note coverage counts and the optional `--redo-thin` policy are accepted;
- Bird cookies, the same-bot identity, Telegram chat/user allowlists, and one
  real callback in the append-only JSONL are verified without logging secrets;
- a mocked fixture, subscription authentication, all tests, and every staged
  unit pass validation;
- native-X video delivery is canaried without overwriting the original, the
  external-video limitation is accepted, and Source-note permissions are
  checked;
- the maintenance timer, audited DLQ/committed-effect recovery, Telegram
  append-only callback replay, retention, disk floor, and the rollback
  procedure above pass their production checks.

A Bird payload that reports video without a usable `media[].videoUrl` remains
retryable rather than being marked done. No unit may be copied, enabled, or
started merely because its files exist in the repository.

## Tests

```bash
pytest -q tests/bookmark_automation
ruff check bookmark_automation tests/bookmark_automation
```
