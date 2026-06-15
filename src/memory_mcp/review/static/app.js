const state = {
  selectedId: null,
};

const statusEl = document.querySelector("#status");
const listEl = document.querySelector("#candidateList");
const detailEl = document.querySelector("#detail");
const filtersEl = document.querySelector("#filters");

filtersEl.addEventListener("submit", (event) => {
  event.preventDefault();
  loadCandidates();
});

async function loadCandidates() {
  setStatus("Loading candidates");
  const params = new URLSearchParams(new FormData(filtersEl));
  for (const [key, value] of [...params.entries()]) {
    if (!value) params.delete(key);
  }
  const data = await request(`/api/candidates?${params.toString()}`);
  listEl.innerHTML = "";
  data.candidates.forEach((candidate) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `candidate-row ${candidate.id === state.selectedId ? "active" : ""}`;
    row.innerHTML = `
      <div class="row-title">${escapeHtml(candidate.lesson)}</div>
      <div class="row-meta">${escapeHtml(candidate.category)} · ${candidate.confidence.toFixed(2)} · ${escapeHtml(candidate.status)}</div>
      <div class="row-meta">${escapeHtml(candidate.situation)}</div>
    `;
    row.addEventListener("click", () => selectCandidate(candidate.id));
    listEl.append(row);
  });
  setStatus(`${data.candidates.length} candidate${data.candidates.length === 1 ? "" : "s"}`);
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
          ${textarea("situation", "Situation", candidate.situation)}
          ${textarea("lesson", "Lesson", candidate.lesson)}
          ${textarea("action", "Action", candidate.action)}
          <label>Category<input name="category" value="${escapeAttr(candidate.category)}"></label>
          <label>Confidence<input name="confidence" type="number" min="0" max="1" step="0.05" value="${candidate.confidence}"></label>
          ${textarea("creation_reason", "Creation Reason", candidate.creation_reason)}
          ${textarea("evidence_summary", "Evidence Summary", candidate.evidence_summary)}
          <div class="actions">
            <button type="submit">Save</button>
            <button type="button" id="approve">Approve</button>
            <button type="button" class="danger" id="reject">Reject</button>
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
    await saveCandidate(candidate.id);
  });
  document.querySelector("#approve").addEventListener("click", () => approveCandidate(candidate.id));
  document.querySelector("#reject").addEventListener("click", () => rejectCandidate(candidate.id));
  document.querySelector("#toggleRaw").addEventListener("click", () => {
    selectCandidate(candidate.id, !includeSegmentEvents);
  });
  const retry = document.querySelector("#retrySegment");
  if (retry) {
    retry.addEventListener("click", () => retrySegment(data.source_segment.id));
  }
}

async function saveCandidate(candidateId) {
  const body = editorBody();
  await request(`/api/candidates/${candidateId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  setStatus("Saved");
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
  const reason = window.prompt("Reject reason");
  if (!reason) return;
  await request(`/api/candidates/${candidateId}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
  setStatus("Rejected");
  detailEl.innerHTML = `<div class="empty">Candidate rejected.</div>`;
  state.selectedId = null;
  loadCandidates();
}

async function retrySegment(segmentId) {
  await request(`/api/segments/${segmentId}/retry`, { method: "POST" });
  setStatus("Segment queued for extraction");
  selectCandidate(state.selectedId);
}

function editorBody() {
  const form = new FormData(document.querySelector("#editor"));
  return {
    situation: form.get("situation"),
    lesson: form.get("lesson"),
    action: form.get("action"),
    category: form.get("category"),
    confidence: Number(form.get("confidence")),
    creation_reason: form.get("creation_reason"),
    evidence_summary: form.get("evidence_summary"),
  };
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
