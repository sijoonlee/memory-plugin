# Plan 3 — Shared Memory Registry (git-versioned, local-first)

Next major phase, split out of `plan-2.md` because it is large enough to be its own
project. This is the **multi-user / sharing** layer: the local store (Plan 1–2) stays
the private source of truth; this plan adds an **optional shared registry** that a team
can publish memories to and pull from.

> Status: **draft / brainstorm.** Direction is settled at the architecture level
> (git-versioned files + thin reconciler, no server vector store); the milestone
> breakdown is not yet finalized. Do not start until the local MVP (Plan 2) is stable.

---

## 0. Decisions taken so far (this round)

From the design discussion that produced this file:

- **Audience: small team.** Not personal-sync-only, not public/community. This is what
  makes identity/access-control worth building but keeps it lightweight (reuse a git
  host's identity + repo permissions rather than a bespoke authz system).
- **V1 scope: push + pull** (not push-only). The team wants cached shared memories
  locally with conflict-at-read, so the pull path — and its complexity — is in V1.
- **Repo layout: monorepo.** The registry server/bot and the `SharedMemoryClient` live
  in this repo alongside `memory_mcp`, for direct model reuse.
- **Pivot to git-versioned storage.** The original M22 sketch assumed a custom backend
  (FastAPI + Postgres/pgvector). The discussion pivoted to **memories as git-versioned
  markdown files** — git gives versioning, audit, identity, access control, and delta
  sync essentially for free. Postgres/pgvector is **not** the storage layer; at most a
  reconciler job embeds ephemerally (see §3). **No persistent server-side vector store.**

---

## 1. Prior art — how Letta does it (`/Users/sijoonlee/Documents/letta`)

Letta (ex-MemGPT) already ships a git-backed memory store; reading it validated the
direction and supplied concrete patterns. Key findings:

- **Memory unit = a "block"** (`schemas/block.py`): `description` + `value` + `metadata`
  + `read_only`. Equivalent to our `when_useful` / `details`.
- **Two parallel version systems:**
  - DB-side `BlockHistory` (`orm/block_history.py`) — SQL snapshots with a monotonic
    `sequence_number` + actor info, for fast undo/redo.
  - Git-side repo (`services/memory_repo/`) — a real git repo, blocks serialized as
    **markdown + YAML frontmatter** (`block_markdown.py`), commits carrying
    `sha`/`parent_sha`/`author_type`/`author_id`/`timestamp` (`MemoryCommit`).
- **Serialization is basically our `MEMORY.md` format:** `description` in frontmatter,
  `value` as the body; non-default fields only. `merge_frontmatter_with_body` preserves
  human hand-formatting on update. They dropped `limit` as "deprecated for git-base memory."
- **Storage is swappable** behind a `StorageBackend` (`storage/base.py`): GCS/S3 for
  cloud, **`LocalStorageBackend` (`storage/local.py`) for OSS/self-host** → real git
  repos on the local filesystem at `~/.letta/memfs/{org}/{agent}/repo.git/`, one repo
  per agent, each block a `{label}.md`. `GitOperations` downloads the bare repo to a
  tempdir, runs the **git CLI**, uploads deltas back.
- **Client sync = git smart-HTTP.** Clients talk to repos directly via git over HTTP
  (the REST "sync" schemas were removed).
- **Multi-writer correctness = a Redis per-repo lock** (`_commit_with_lock`) + delta
  uploads.

**Where we diverge from Letta:** their git repo is **per-agent** and purely for
*versioning one agent's memory*; cross-entity *sharing* is a separate mechanism (blocks
attached to multiple agents **by reference** within an org). They therefore never face
**cross-user merge** of near-duplicate memories — because blocks are deliberate, not
auto-extracted. Our registry pools auto-extracted memories from multiple authors, so the
**dedup/merge + shared-identity model (§3–4) is ours to build** and is the genuinely new
part.

**What we reuse conceptually:** markdown+frontmatter serialization; git as the
version/audit truth; a fast DB index *alongside* git (not as the query path); the same
engine running local or remote. **What we drop:** the `StorageBackend` abstraction —
memory-mcp is truly local-first (no server in the local path), so there is only ever one
local folder; and Letta's per-agent scoping (ours is per-team/project/global).

---

## 2. Core principle — the registry is a file store, not a brain

The single most important simplification: **all embedding and search are local.** The
local app pulls files, embeds them with its own model, and searches its own vector store
over the union of local + shared memories. The registry **never answers a semantic
query**, so:

- the registry needs **no vector store** and **no serving-time embedding model**;
- it needs **no events, no candidates, no extraction** (those stay local — the
  privacy-bearing pipeline never leaves the machine);
- it is, in essence, a **versioned, organized tree of memory files + a manifest**.

The one piece of server-side intelligence is the **reconciler** (§3), which embeds
**ephemerally during its run** to merge near-duplicates. It keeps no persistent index and
serves no queries — so "no server vector store" still holds.

---

## 3. The registry = git file store + manifest + reconciler

```
LOCAL (private, full pipeline)              SHARED REGISTRY (git repo)
  events ──never leaves──
  local memories ──publish(opt-in, redacted)──►   {type}/{shared_id}.md  (frontmatter+body)
       ▲                                           index.json   (manifest for delta sync)
       │                                           ── reconciler job (the only compute) ──
       └── pull: cache origin=shared, re-embed ◄── embeds ephemerally → merges near-dups,
  retrieval = local search over (local ∪ shared)   writes merged_into aliases, bumps
                                                    version, tallies contributors
```

### 3a. Storage layout (a git repo)

```
registry/                          (a git repo: private GitHub repo OR self-hosted bare repo)
  index.json                       manifest: shared_id → {version, type, project, path,
                                             merged_into, contributors, content_hash}
  user/<shared_id>.md
  feedback/<shared_id>.md
  project/<project-key>/<shared_id>.md
  reference/<shared_id>.md
```

Each `.md` is our existing format — frontmatter (`shared_id`, `when_useful`,
`memory_type`, `tags`, `project`, `contributors`, `version`, `merged_into`,
`created_at`, `updated_at`) + body (`details`). `index.json` plays the same role as the
local `MEMORY.md` index / M20 catalog: it makes pull a cheap diff instead of a tree walk.

### 3b. The reconciler (the only server-side compute)

A periodic / on-push job — most cheaply a **GitHub Action with repo access** (this *is*
the "online server with GitHub access"). It:

1. embeds the corpus ephemerally,
2. finds near-duplicates that clients' fast-path dedup missed (cross-user races),
3. merges them into a canonical memory, writes `merged_into` aliases on the losers,
4. bumps `version`, tallies `contributors`,
5. commits the result back (git history = audit trail).

It is the **merge authority** — the reason pure client-side dedup is insufficient (two
users publishing the same lesson before either pulls the other would never converge
without an authority).

---

## 4. Identity & sync model — `shared_id` / `version` / `merged_into`

Three requirements drive this: (a) cross-user dedup/merge, (b) history, (c) the local app
must detect upstream updates and refresh. All three force a **registry-assigned canonical
identity** distinct from the local id.

### 4a. Registry-side per-memory fields
- **`shared_id`** — canonical, registry-assigned (e.g. `smem_<uuid>`), stable across
  users and across the local/remote boundary. Without it the local app can't tell
  "updated upstream" from "brand new."
- **`version`** — monotonic int, bumped on any content change or merge. (Plus optional
  `content_hash` for cheap change detection.)
- **`merged_into`** — `null` for live memories; a `shared_id` for a memory the reconciler
  consolidated. A merged memory becomes a **tombstone with a redirect**; the alias chain
  *is* the cross-user merge lineage.
- **`contributors`** — author identities; count ranks canonical memories.

### 4b. Local-side additions (cache lane)
Mirror the M22 two-lane idea, keyed by `shared_id`:
- `origin` — `local` | `shared` (writable vs read-only)
- `shared_id` — `NULL`, or `smem_...` once published or pulled
- `shared_version` — last synced version
- `origin=shared` rows are read-only; local feedback still recorded separately
  (`feedback_events`), so it survives a refresh — **tie-break: local wins.**

### 4c. The three flows

**Publish** (local → registry): redact → human-visible preview/diff of exact bytes →
client fast-path dedup against the already-pulled corpus (obvious dup of `smem_X` →
add self to `contributors`, bump `version`; else mint a new `shared_id`) → commit →
push (per-scope lock serializes concurrent pushes). Strip local provenance
(`evidence_event_ids` / `source_session_segment_id` don't resolve off-machine); send
`evidence_summary` text only.

**Pull** (registry → local): `git pull` → diff `index.json` against local
`(shared_id → shared_version)`:
- `merged_into` set & I cached the old id → swap to the target, carry my local feedback;
- new `shared_id` → fetch, **embed locally**, insert `origin=shared`;
- `version` increased → re-fetch, re-embed, update the cached row;
- unchanged → skip.

**Reconcile** (registry-internal): see §3b. The authority for cross-user merge.

History = git commits; identity = `shared_id`; freshness = `version`; merge lineage =
`merged_into` chain.

---

## 5. Where the repo lives — two options (defer the pick)

- **Private GitHub repo** *(leaning)* — zero hosted infra; free identity (commit
  author), access control (collaborators/teams), PR review, and delta sync. Cost:
  per-user GitHub auth; **secrets in history are permanent**, so redaction is mandatory
  and gated pre-push; GitHub API rate limits (fine at small-team scale).
- **Self-hosted bare git repo over smart-HTTP** (Letta's memfs approach, minus object
  storage) — full control, no GitHub dependency. Cost: run/secure one small service +
  its own auth.

Recommendation: **start on a private GitHub repo**; it defers all custom server infra
until a real limit is felt. Reconciler = a GitHub Action in that repo.

---

## 6. Privacy invariant (carried from M22 — unchanged)

**Private by default; local-first; sharing is explicit, per-memory, never automatic.**
Enforced by architecture, not policy:
- the local pipeline (`events → memory → archive/delete`) never touches the network;
- **events never leave the machine** — only `active`, human-kept, redacted *memories*
  are eligible to share;
- nothing publishes except what the user explicitly selects, one action at a time;
- a `never_share` flag can pin a memory permanently local;
- **redaction (M11) is mandatory at the publish boundary** — doubly so against a GitHub
  history that can't be easily scrubbed.

---

## 7. Open questions (resolve while detailing milestones)

- **Dedup similarity threshold + algorithm** (cosine over `when_useful`? over full
  `details`? both?), and whether the reconciler ever does an *LLM synthesis* merge vs
  pure consolidation.
- **Scope model:** how `index.json` partitions per-team vs per-project vs global; one
  repo with directories vs a repo per scope.
- **Conflict on contradiction** (not just near-duplicate): surface both with origin,
  never auto-supersede across the boundary — but how does the local catalog present it?
- **Retract/unshare semantics** against an immutable git history (tombstone vs history
  rewrite).
- **Manual hand-edits** to registry files (PR review): reuse Letta's
  `merge_frontmatter_with_body` idea so a human edit isn't clobbered by the next push.
- **Auth UX** for the GitHub path: per-user token, GitHub App, or a thin proxy.

---

## 8. Rough sequencing (draft)

1. **Local git-versioning first** *(independently shippable, no server)* — serialize
   local memories to `{shared_id?}.md` frontmatter files + a local git repo + the DB
   index alongside. Proves the format and the file/DB split with zero sharing risk.
2. **Push** — publish action (redaction + preview), `shared_id` minting, commit/push to
   the registry repo, client fast-path dedup, `shared_at` markers.
3. **Pull + cache lane** — `origin`/`shared_id`/`shared_version` columns, `index.json`
   diff, local re-embed, `merged_into` alias resolution, local-wins feedback.
4. **Reconciler** — the GitHub Action: ephemeral embedding, cross-user merge, contributor
   tally. (Can land alongside push if cross-user races bite early.)
5. **Registry UI / ops** — browse shared memories, contributor counts, steward actions;
   the **local review UI stays** as the local manager.

Each step is independently shippable; the local pipeline is never put on the network.
