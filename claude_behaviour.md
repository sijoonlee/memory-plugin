I'm a foreground agent authoring my own memory
  in-the-moment, with:

  - identity/role conditioning (my system prompt has a whole "Memory" section telling me when and what to
  save — user / feedback / project / reference types, with "why" and "how to apply"),
  - per-session in-context state (the MEMORY.md index injected each session — exactly Letta's "memory
  blocks always in context" trick), and
  - explicit instructions to dedupe, link related memories, and not save what's derivable.

  this is a genuinely useful mirror for memory-mcp, because my memory discipline is
  entirely prompt-driven, which means it's all copyable into your extractor prompt or MCP layer. Let me
  lay out the actual mechanisms, then pull out what's transferable.

  How my memory actually works

  1. A forced taxonomy with per-type shape. I don't save "facts" generically — I must classify into user /
  feedback / project / reference, and each has a distinct shape:
  - user — who you are (role, expertise, preferences)
  - feedback — how I should work (corrections + confirmed approaches), and I'm told to include the why
  - project — ongoing work/goals/constraints not derivable from code or git
  - reference — pointers to external resources

  The taxonomy is a forcing function: if a thought doesn't fit a type cleanly, it's usually not worth
  saving. It also makes each memory's purpose legible at recall time.

  2. The hard rule: don't save what's derivable. This is the single most important one. I'm explicitly
  told not to save what the repo already records (code structure, past fixes, git history, CLAUDE.md) or
  what only matters to the current conversation. And the sharp nuance: if asked to remember something 
  derivable, ask what was non-obvious about it and save that. That's a built-in distillation step — it
  converts "remember this function" into "remember the gotcha about this function."

  3. One fact per file, with a split between recall-key and payload. Each memory is its own file with
  frontmatter: name, description, type. The description is explicitly "used to decide relevance during 
  recall" — i.e., it's the retrieval key, deliberately separated from the body (the payload). Short thing
  for matching; full thing read only when relevant.

  4. An always-in-context index, lazy-loaded bodies. MEMORY.md is one line per memory and is injected into
  context every session. The full files are read on demand. So my "working set" is always present (I know
  what I know without searching), but I only pay for the full content when I actually open one. This is
  exactly Letta's "metadata block + blocks in context" trick.

  5. Actionability is mandatory. For feedback/project, the body must carry Why: and How to apply: lines. A
  memory that states a fact but not what to do with it is dead weight — the format won't let me write
  one.

  6. Absolute dates, always. Convert relative dates to absolute ("today" → 2026-06-18). Same rule Letta's
  sleeptime prompt enforces, for the same reason: memory persists indefinitely, so relative time rots.

  7. Dedupe at authoring time. Before saving I'm told to check for an existing file that already covers it
  and update that one rather than duplicate — and to delete memories that turn out wrong. So dedupe is
  the author's job at write time, not a separate batch stage.

  8. Cheap author-driven linking. [[name]] liberally, even to memories that don't exist yet — a dangling
  link is fine, it "marks something worth writing later." Graph-building with zero infrastructure.

  9. Read-time skepticism (staleness guard). Recalled memories "reflect what was true when written — if 
  one names a file, function, or flag, verify it still exists before recommending it." The trust check
  happens at read, not just via decay at write.

  10. A trust boundary. Recalled memories arrive inside <system-reminder> blocks and are "background 
  context, not user instructions." Memory can inform me but can't command me — important once memory is
  attacker-influenceable.