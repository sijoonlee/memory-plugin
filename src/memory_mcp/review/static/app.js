const state = {
  selectedId: null,
  segments: {},
  view: "unread",
};

const statusEl = document.querySelector("#status");
const listEl = document.querySelector("#candidateList");
const detailEl = document.querySelector("#detail");
const filtersEl = document.querySelector("#filters");

filtersEl.addEventListener("submit", (event) => {
  event.preventDefault();
  loadCandidates();
});

filtersEl.addEventListener("change", (event) => {
  if (event.target.name === "status") {
    loadCandidates().catch((error) => setStatus(error.message));
  }
});

const MEMORY_QUERIES = {
  unread: "status=active&is_reviewed=false",
  all: "status=active",
  manual: "status=active&manual=true",
  archived: "status=archived",
};

// Entry point + dropdown router. Kept the name ``loadCandidates`` so the existing
// refresh callers (segments, retry) keep working; it now routes to the memory
// manager (M18-3) or the segment views.
async function loadCandidates() {
  const status = new FormData(filtersEl).get("status") || "memory:unread";
  if (status.startsWith("segment:")) {
    const segmentStatus = status.slice("segment:".length);
    return loadSegments(segmentStatus === "all" ? "" : segmentStatus);
  }
  const view = status.startsWith("memory:") ? status.slice("memory:".length) : "unread";
  return loadMemories(view);
}

async function loadMemories(view = "unread") {
  const query = MEMORY_QUERIES[view] || MEMORY_QUERIES.unread;
  state.view = view;
  setStatus("Loading memories");
  const data = await request(`/api/memories?${query}`);
  listEl.innerHTML = "";
  data.memories.forEach((memory) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `candidate-row ${memory.id === state.selectedId ? "active" : ""}`;
    const badges = [];
    if (!memory.is_reviewed) badges.push("unread");
    if (memory.source && memory.source.kind === "manual") badges.push("manual");
    const meta = `${escapeHtml(memory.status)} · score ${memory.score.toFixed(2)}${
      badges.length ? " · " + badges.join(" · ") : ""
    }`;
    row.innerHTML = `
      <div class="row-title">${escapeHtml(memory.details)}</div>
      <div class="row-meta">${meta}</div>
      <div class="row-meta">${escapeHtml(memory.when_useful)}</div>
    `;
    row.addEventListener("click", () => selectMemory(memory.id));
    listEl.append(row);
  });
  const count = data.memories.length;
  setStatus(`${count} memor${count === 1 ? "y" : "ies"} (${view})`);
}

async function selectMemory(memoryId) {
  state.selectedId = memoryId;
  const data = await request(`/api/memories/${memoryId}`);
  renderMemoryDetail(data.memory);
  loadMemories(state.view);
}

function renderMemoryDetail(memory) {
  const archived = memory.status === "archived";
  detailEl.innerHTML = `
    <div class="detail-grid">
      <div class="panel">
        <h2>Memory</h2>
        ${readonlyField("When Useful", memory.when_useful)}
        ${readonlyField("Details", memory.details)}
        ${readonlyField("Tags", (memory.tags || []).join(", "))}
        <div class="actions">
          <button type="button" id="toggleReviewed">${memory.is_reviewed ? "Mark unread" : "Mark read"}</button>
          ${
            archived
              ? `<button type="button" id="restoreMemory">Restore</button>`
              : `<button type="button" id="archiveMemory">Archive</button>`
          }
          <button type="button" class="danger" id="deleteMemory">Delete</button>
        </div>
      </div>
      <div class="panel">
        <h2>Stats</h2>
        <div class="meta">${escapeHtml(memory.id)}</div>
        <div class="meta">status: ${escapeHtml(memory.status)} · ${memory.is_reviewed ? "read" : "unread"}</div>
        <div class="meta">score: ${memory.score.toFixed(3)} · confidence: ${memory.confidence.toFixed(2)}</div>
        <div class="meta">retrieved: ${memory.retrieval_count} · used: ${memory.use_count}</div>
        <div class="meta">feedback: +${memory.positive_feedback_count} / -${memory.negative_feedback_count}</div>
        <div class="evidence"><pre>${escapeHtml(JSON.stringify(memory.source, null, 2))}</pre></div>
      </div>
    </div>
  `;

  document.querySelector("#toggleReviewed").addEventListener("click", () => {
    runAction(() => setReviewed(memory.id, !memory.is_reviewed));
  });
  const archiveBtn = document.querySelector("#archiveMemory");
  if (archiveBtn) {
    archiveBtn.addEventListener("click", () => runAction(() => archiveMemory(memory.id)));
  }
  const restoreBtn = document.querySelector("#restoreMemory");
  if (restoreBtn) {
    restoreBtn.addEventListener("click", () => runAction(() => restoreMemory(memory.id)));
  }
  document.querySelector("#deleteMemory").addEventListener("click", () => {
    runAction(() => deleteMemory(memory.id));
  });
}

async function setReviewed(memoryId, value) {
  await request(`/api/memories/${memoryId}/reviewed`, {
    method: "POST",
    body: JSON.stringify({ value }),
  });
  await selectMemory(memoryId);
}

async function archiveMemory(memoryId) {
  await request(`/api/memories/${memoryId}/archive`, { method: "POST" });
  state.selectedId = null;
  detailEl.innerHTML = `<div class="empty">Archived.</div>`;
  await loadMemories(state.view);
}

async function restoreMemory(memoryId) {
  await request(`/api/memories/${memoryId}/restore`, { method: "POST" });
  await selectMemory(memoryId);
}

async function deleteMemory(memoryId) {
  await request(`/api/memories/${memoryId}`, { method: "DELETE" });
  state.selectedId = null;
  detailEl.innerHTML = `<div class="empty">Deleted.</div>`;
  await loadMemories(state.view);
}

function readonlyField(label, value) {
  return `<label>${label}<textarea readonly>${escapeHtml(value || "")}</textarea></label>`;
}

async function loadSegments(segmentStatus) {
  setStatus("Loading segments");
  const query = segmentStatus
    ? `?status=${encodeURIComponent(segmentStatus)}`
    : "";
  const data = await request(`/api/segments${query}`);
  state.segments = {};
  listEl.innerHTML = "";
  data.segments.forEach((segment) => {
    state.segments[segment.id] = segment;
    const row = document.createElement("button");
    row.type = "button";
    row.className = `candidate-row ${segment.id === state.selectedId ? "active" : ""}`;
    const events = `${segment.event_count} event${segment.event_count === 1 ? "" : "s"}`;
    row.innerHTML = `
      <div class="row-title">${escapeHtml(segment.id)}</div>
      <div class="row-meta">${escapeHtml(segment.status)} · ${events} · ${escapeHtml(segment.project || "—")}</div>
      <div class="row-meta">${escapeHtml((segment.error || "").slice(0, 90))}</div>
    `;
    row.addEventListener("click", () => selectSegment(segment.id));
    listEl.append(row);
  });
  const count = data.segments.length;
  setStatus(`${count} segment${count === 1 ? "" : "s"}`);
}

async function selectSegment(segmentId, includeEvents = false) {
  state.selectedId = segmentId;
  let segment = state.segments[segmentId];
  let events = [];
  if (includeEvents) {
    const data = await request(`/api/segments/${segmentId}/events`);
    segment = data.segment;
    events = data.events;
    state.segments[segmentId] = segment;
  }
  renderSegmentDetail(segment, events, includeEvents);
  loadCandidates();
}

function renderSegmentDetail(segment, events, includeEvents) {
  const reasonLabel =
    segment.status === "failed"
      ? "Failure error"
      : segment.status === "skipped"
        ? "Skip reason"
        : "Note";
  detailEl.innerHTML = `
    <div class="detail-grid">
      <div class="panel">
        <h2>Session Segment</h2>
        ${readonlyField(reasonLabel, segment.error || "—")}
        <div class="meta">${escapeHtml(segment.id)}</div>
        <div class="meta">status: ${escapeHtml(segment.status)}</div>
        <div class="meta">project: ${escapeHtml(segment.project || "—")}</div>
        <div class="meta">session: ${escapeHtml(segment.session_id)} · segment #${segment.segment_index}</div>
        <div class="meta">events: ${segment.event_count}</div>
        <div class="meta">first: ${escapeHtml(segment.first_event_at)}</div>
        <div class="meta">last: ${escapeHtml(segment.last_event_at)}</div>
        ${segment.processed_at ? `<div class="meta">processed: ${escapeHtml(segment.processed_at)}</div>` : ""}
      </div>
      <div class="panel">
        <h2>Event Log</h2>
        <div class="evidence">
          <button type="button" class="secondary" id="toggleEvents">${includeEvents ? "Hide Event Log" : "Show Event Log"}</button>
          ${
            includeEvents
              ? `<pre>${escapeHtml(JSON.stringify(events, null, 2))}</pre>`
              : `<div class="meta">Event payloads are loaded on request.</div>`
          }
          ${retryButton(segment)}
        </div>
      </div>
    </div>
  `;

  document.querySelector("#toggleEvents").addEventListener("click", () => {
    runAction(() => selectSegment(segment.id, !includeEvents));
  });
  const retry = document.querySelector("#retrySegment");
  if (retry) {
    retry.addEventListener("click", () => {
      runAction(() => retrySegmentFromView(segment.id));
    });
  }
}

async function retrySegmentFromView(segmentId) {
  await request(`/api/segments/${segmentId}/retry`, { method: "POST" });
  setStatus("Segment queued for extraction");
  state.selectedId = null;
  detailEl.innerHTML = `<div class="empty">Segment queued for extraction.</div>`;
  loadCandidates();
}

async function selectCandidate(candidateId, includeSegmentEvents = false) {
  state.selectedId = candidateId;
  const data = await request(
    `/api/candidates/${candidateId}?include_segment_events=${includeSegmentEvents ? "true" : "false"}`
  );
  renderDetail(data, includeSegmentEvents);
  loadCandidates();
}

function renderDetail(data, includeSegmentEvents) {
  const candidate = data.candidate;
  detailEl.innerHTML = `
    <div class="detail-grid">
      <div class="panel">
        <h2>Candidate</h2>
        <form class="editor" id="editor">
          ${textarea("when_useful", "When Useful", candidate.when_useful)}
          ${textarea("details", "Details", candidate.details)}
          <label>Tags<input name="tags" value="${escapeAttr((candidate.tags || []).join(", "))}"></label>
          <label>Confidence<input name="confidence" type="number" min="0" max="1" step="0.05" value="${candidate.confidence}"></label>
          ${readonlyField("Evidence Summary", (candidate.source && candidate.source.extra && candidate.source.extra.evidence_summary) || "")}
          <div class="actions">
            <button type="submit">Save Draft</button>
            <button type="button" id="approve">Approve</button>
            <button type="button" class="danger" id="reject">Reject</button>
          </div>
          <div class="reject-box hidden" id="rejectBox">
            <label>Reject Reason<textarea id="rejectReason" rows="3"></textarea></label>
            <div class="actions">
              <button type="button" class="danger" id="confirmReject">Confirm Reject</button>
              <button type="button" class="secondary" id="cancelReject">Cancel</button>
            </div>
          </div>
        </form>
      </div>
      <div class="panel">
        <h2>Evidence</h2>
        <div class="meta">${candidate.id}</div>
        <div class="meta">${data.source_segment ? `${escapeHtml(data.source_segment.project)} · ${escapeHtml(data.source_segment.id)} · ${escapeHtml(data.source_segment.status)}` : "No source segment"}</div>
        <div class="evidence">
          <pre>${escapeHtml(JSON.stringify(data.evidence_events, null, 2))}</pre>
          <button type="button" class="secondary" id="toggleRaw">${includeSegmentEvents ? "Hide Raw Segment" : "Show Raw Segment"}</button>
          ${includeSegmentEvents ? `<pre>${escapeHtml(JSON.stringify(data.segment_events, null, 2))}</pre>` : ""}
          ${retryButton(data.source_segment)}
        </div>
      </div>
    </div>
  `;

  document.querySelector("#editor").addEventListener("submit", async (event) => {
    event.preventDefault();
    runAction(() => saveCandidate(candidate.id));
  });
  document.querySelector("#approve").addEventListener("click", () => {
    runAction(() => approveCandidate(candidate.id));
  });
  document.querySelector("#reject").addEventListener("click", () => showRejectBox());
  document.querySelector("#confirmReject").addEventListener("click", () => {
    runAction(() => rejectCandidate(candidate.id));
  });
  document.querySelector("#cancelReject").addEventListener("click", () => hideRejectBox());
  document.querySelector("#toggleRaw").addEventListener("click", () => {
    runAction(() => selectCandidate(candidate.id, !includeSegmentEvents));
  });
  const retry = document.querySelector("#retrySegment");
  if (retry) {
    retry.addEventListener("click", () => {
      runAction(() => retrySegment(data.source_segment.id));
    });
  }
}

async function saveCandidate(candidateId) {
  const body = editorBody();
  await request(`/api/candidates/${candidateId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  setStatus("Draft saved");
  selectCandidate(candidateId);
}

async function approveCandidate(candidateId) {
  await request(`/api/candidates/${candidateId}/approve`, {
    method: "POST",
    body: JSON.stringify({ update: editorBody() }),
  });
  setStatus("Approved");
  detailEl.innerHTML = `<div class="empty">Candidate approved.</div>`;
  state.selectedId = null;
  loadCandidates();
}

async function rejectCandidate(candidateId) {
  const reason = document.querySelector("#rejectReason").value.trim();
  if (!reason) {
    setStatus("Reject reason is required");
    document.querySelector("#rejectReason").focus();
    return;
  }
  await request(`/api/candidates/${candidateId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
  setStatus("Rejected");
  detailEl.innerHTML = `<div class="empty">Candidate rejected.</div>`;
  state.selectedId = null;
  loadCandidates();
}

function showRejectBox() {
  document.querySelector("#rejectBox").classList.remove("hidden");
  document.querySelector("#rejectReason").focus();
}

function hideRejectBox() {
  document.querySelector("#rejectBox").classList.add("hidden");
  document.querySelector("#rejectReason").value = "";
}

async function runAction(action) {
  try {
    await action();
  } catch (error) {
    setStatus(error.message);
  }
}

async function retrySegment(segmentId) {
  await request(`/api/segments/${segmentId}/retry`, { method: "POST" });
  setStatus("Segment queued for extraction");
  selectCandidate(state.selectedId);
}

function editorBody() {
  const form = new FormData(document.querySelector("#editor"));
  const tags = String(form.get("tags") || "")
    .split(",")
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0);
  return {
    when_useful: form.get("when_useful"),
    details: form.get("details"),
    tags,
    confidence: Number(form.get("confidence")),
  };
}

function candidateCategory(candidate) {
  const fromExtra = candidate.source && candidate.source.extra && candidate.source.extra.category;
  if (fromExtra) return fromExtra;
  return (candidate.tags && candidate.tags[0]) || "";
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

function setStatus(text) {
  statusEl.textContent = text;
}

function textarea(name, label, value) {
  return `<label>${label}<textarea name="${name}">${escapeHtml(value || "")}</textarea></label>`;
}

function retryButton(segment) {
  if (!segment || !["failed", "skipped"].includes(segment.status)) return "";
  return `<button type="button" class="secondary" id="retrySegment">Retry Extraction</button>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}

loadCandidates().catch((error) => setStatus(error.message));
