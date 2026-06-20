from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from memory_mcp.core.events import EventRecord, SessionSegmentRecord
from memory_mcp.core.models import MemoryRecord

MemoryCandidateCategory = Literal[
    "clue_location",
    "external_context",
    "user_correction",
    "durable_workflow",
    "repeated_pitfall",
]


class ExtractedMemoryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    situation: str = Field(min_length=1)
    lesson: str = Field(min_length=1)
    action: str = Field(min_length=1)
    category: MemoryCandidateCategory
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_event_ids: list[str]
    evidence_summary: str = Field(min_length=1)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[ExtractedMemoryCandidate]
    no_memory_reason: str | None

    @model_validator(mode="after")
    def require_reason_when_empty(self) -> ExtractionResult:
        if not self.candidates and not self.no_memory_reason:
            raise ValueError("no_memory_reason is required when candidates is empty")
        return self


class MergeProposalResult(BaseModel):
    """An LLM proposal for whether and how to merge related candidates."""

    model_config = ConfigDict(extra="forbid")

    should_merge: bool
    reason: str = Field(min_length=1)
    situation: str = ""
    lesson: str = ""
    action: str = ""
    category: MemoryCandidateCategory | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_summary: str = ""

    @model_validator(mode="after")
    def require_content_when_merging(self) -> MergeProposalResult:
        if self.should_merge:
            missing = [
                name
                for name in ("situation", "lesson", "action", "evidence_summary")
                if not getattr(self, name).strip()
            ]
            if missing or self.category is None:
                raise ValueError(
                    "merged content is required when should_merge is true: "
                    + ", ".join(missing + ([] if self.category else ["category"]))
                )
        return self


class MemoryExtractor(Protocol):
    def extract(
        self,
        *,
        segment: SessionSegmentRecord,
        events: list[EventRecord],
    ) -> ExtractionResult:
        ...


class MergeProposer(Protocol):
    def propose(
        self,
        *,
        candidates: list[MemoryRecord],
    ) -> MergeProposalResult:
        ...


class StaticMemoryExtractor:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result

    def extract(
        self,
        *,
        segment: SessionSegmentRecord,
        events: list[EventRecord],
    ) -> ExtractionResult:
        return self.result


class StaticMergeProposer:
    def __init__(self, result: MergeProposalResult) -> None:
        self.result = result

    def propose(
        self,
        *,
        candidates: list[MemoryRecord],
    ) -> MergeProposalResult:
        return self.result


class CodexCliExtractor:
    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        model: str | None = None,
        effort: str | None = None,
        timeout_seconds: int = 180,
        use_project_context: bool = False,
    ) -> None:
        self.codex_bin = codex_bin
        self.model = model
        self.effort = effort
        self.timeout_seconds = timeout_seconds
        self.use_project_context = use_project_context

    def extract(
        self,
        *,
        segment: SessionSegmentRecord,
        events: list[EventRecord],
    ) -> ExtractionResult:
        prompt = build_extraction_prompt(segment=segment, events=events)
        raw_output = _run_codex_exec(
            prompt,
            ExtractionResult.model_json_schema(),
            codex_bin=self.codex_bin,
            model=self.model,
            effort=self.effort,
            timeout_seconds=self.timeout_seconds,
            project_dir=_project_dir(self.use_project_context, segment.project),
        )
        return _parse_structured(raw_output, ExtractionResult, provider="codex exec")


class ClaudeCliExtractor:
    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        model: str | None = None,
        effort: str | None = None,
        timeout_seconds: int = 180,
        use_project_context: bool = False,
    ) -> None:
        self.claude_bin = claude_bin
        self.model = model
        self.effort = effort
        self.timeout_seconds = timeout_seconds
        self.use_project_context = use_project_context

    def extract(
        self,
        *,
        segment: SessionSegmentRecord,
        events: list[EventRecord],
    ) -> ExtractionResult:
        prompt = build_extraction_prompt(segment=segment, events=events)
        raw_output = _run_claude_print(
            prompt,
            ExtractionResult.model_json_schema(),
            claude_bin=self.claude_bin,
            model=self.model,
            effort=self.effort,
            timeout_seconds=self.timeout_seconds,
            project_dir=_project_dir(self.use_project_context, segment.project),
        )
        return _parse_structured(raw_output, ExtractionResult, provider="claude")


class CodexCliMergeProposer:
    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        model: str | None = None,
        effort: str | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        self.codex_bin = codex_bin
        self.model = model
        self.effort = effort
        self.timeout_seconds = timeout_seconds

    def propose(
        self,
        *,
        candidates: list[MemoryRecord],
    ) -> MergeProposalResult:
        prompt = build_merge_prompt(candidates=candidates)
        raw_output = _run_codex_exec(
            prompt,
            MergeProposalResult.model_json_schema(),
            codex_bin=self.codex_bin,
            model=self.model,
            effort=self.effort,
            timeout_seconds=self.timeout_seconds,
            project_dir=None,
        )
        return _parse_structured(raw_output, MergeProposalResult, provider="codex exec")


class ClaudeCliMergeProposer:
    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        model: str | None = None,
        effort: str | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        self.claude_bin = claude_bin
        self.model = model
        self.effort = effort
        self.timeout_seconds = timeout_seconds

    def propose(
        self,
        *,
        candidates: list[MemoryRecord],
    ) -> MergeProposalResult:
        prompt = build_merge_prompt(candidates=candidates)
        raw_output = _run_claude_print(
            prompt,
            MergeProposalResult.model_json_schema(),
            claude_bin=self.claude_bin,
            model=self.model,
            effort=self.effort,
            timeout_seconds=self.timeout_seconds,
            project_dir=None,
        )
        return _parse_structured(raw_output, MergeProposalResult, provider="claude")


def build_extraction_prompt(
    *,
    segment: SessionSegmentRecord,
    events: list[EventRecord],
) -> str:
    payload = {
        "session_segment": segment.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in events],
    }
    return (
        "You are extracting durable memory candidates for a local agent memory system.\n"
        "Return only JSON that matches the provided output schema.\n\n"
        "A memory must be a compact reusable lesson, not a transcript summary.\n"
        "Create candidates only for durable, future-useful facts such as:\n"
        "- clue_location: where a useful code/config/document clue was found after search\n"
        "- external_context: human-provided context that filled a knowledge gap\n"
        "- user_correction: durable correction to an agent assumption or behavior\n"
        "- durable_workflow: project-specific command, workflow, or convention\n"
        "- repeated_pitfall: mistake or trap likely to recur\n\n"
        "Skip candidates that are vague, unresolved, only temporary, or not reusable.\n"
        "If there is no durable memory, return an empty candidates array with no_memory_reason.\n"
        "Use only event ids present in the input as evidence_event_ids.\n\n"
        "<session_events_json>\n"
        f"{json.dumps(payload, indent=2)}\n"
        "</session_events_json>\n"
    )


def compose_details(lesson: str, action: str) -> str:
    """Combine the extractor's ``lesson`` + ``action`` into a memory ``details`` body."""

    lesson = lesson.strip()
    action = action.strip()
    if not action:
        return lesson
    if not lesson:
        return action
    return f"{lesson}\n\nHow to apply: {action}"


def build_merge_prompt(*, candidates: list[MemoryRecord]) -> str:
    payload = [
        {
            "id": candidate.id,
            "when_useful": candidate.when_useful,
            "details": candidate.details,
            "category": (candidate.tags[0] if candidate.tags else None),
            "confidence": candidate.confidence,
            "evidence_summary": candidate.source.extra.get("evidence_summary", ""),
        }
        for candidate in candidates
    ]
    return (
        "You are reviewing memory candidates that a clustering step thinks may "
        "describe the same durable lesson.\n"
        "Return only JSON that matches the provided output schema.\n\n"
        "Decide whether these candidates should be merged into one stronger "
        "memory. Merge only when they truly capture the same reusable lesson; "
        "prefer the clearest situation, the most actionable action, and a lesson "
        "that covers all of them.\n"
        "If they are genuinely distinct, set should_merge to false and explain "
        "why in reason; leave the content fields empty.\n"
        "When merging, pick the single best category and a confidence reflecting "
        "the combined evidence.\n\n"
        "<candidates_json>\n"
        f"{json.dumps(payload, indent=2)}\n"
        "</candidates_json>\n"
    )


_T = TypeVar("_T", bound=BaseModel)


def _project_dir(use_project_context: bool, project: str | None) -> str | None:
    if use_project_context and project and Path(project).exists():
        return project
    return None


def _run_cli_subprocess(
    cmd: list[str],
    *,
    input_text: str | None,
    timeout_seconds: int,
    cwd: Path,
    provider: str,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{provider} timed out after {timeout_seconds}s") from exc

    if completed.returncode != 0:
        raise RuntimeError(
            f"{provider} failed (exit={completed.returncode}): "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed


def _run_codex_exec(
    prompt: str,
    schema: dict,
    *,
    codex_bin: str,
    model: str | None,
    effort: str | None,
    timeout_seconds: int,
    project_dir: str | None,
) -> str:
    with tempfile.TemporaryDirectory(prefix="memory-mcp-cli-") as temp_dir:
        temp_path = Path(temp_dir)
        schema_path = temp_path / "schema.json"
        output_path = temp_path / "codex-output.json"
        schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")
        cmd = [
            codex_bin,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        if model is not None:
            cmd.extend(["--model", model])
        if effort is not None:
            cmd.extend(["--config", f'model_reasoning_effort="{effort}"'])
        if project_dir is not None:
            cmd.extend(["--cd", project_dir])
        cmd.append("-")

        _run_cli_subprocess(
            cmd,
            input_text=prompt,
            timeout_seconds=timeout_seconds,
            cwd=temp_path,
            provider="codex exec",
        )
        if not output_path.exists():
            raise RuntimeError("codex exec did not write output-last-message file")
        return output_path.read_text(encoding="utf-8").strip()


def _run_claude_print(
    prompt: str,
    schema: dict,
    *,
    claude_bin: str,
    model: str | None,
    effort: str | None,
    timeout_seconds: int,
    project_dir: str | None,
) -> str:
    schema_json = json.dumps(schema)
    with tempfile.TemporaryDirectory(prefix="memory-mcp-cli-") as temp_dir:
        temp_path = Path(temp_dir)
        cmd = [
            claude_bin,
            # NOTE: do NOT use --bare here. It bundles "skip keychain reads"
            # along with its other minimal-mode behaviors, which breaks auth
            # on machines whose OAuth token lives only in the macOS Keychain
            # (no ~/.claude/.credentials.json). Isolation is already provided
            # by --no-session-persistence plus the tempdir cwd below.
            "--print",
            "--output-format",
            "json",
            "--json-schema",
            schema_json,
            "--no-session-persistence",
        ]
        if model is not None:
            cmd.extend(["--model", model])
        if effort is not None:
            cmd.extend(["--effort", effort])
        if project_dir is not None:
            cmd.extend(["--add-dir", project_dir])
        cmd.append(prompt)

        completed = _run_cli_subprocess(
            cmd,
            input_text=None,
            timeout_seconds=timeout_seconds,
            cwd=temp_path,
            provider="claude",
        )
        return completed.stdout.strip()


def _parse_structured(raw_output: str, model: type[_T], *, provider: str) -> _T:
    try:
        return model.model_validate_json(raw_output)
    except ValidationError as direct_error:
        try:
            envelope = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{provider} returned invalid JSON: {direct_error}"
            ) from exc

        if isinstance(envelope, dict):
            # `--output-format json` wraps the reply in an envelope; with
            # `--json-schema` the validated object lands in `structured_output`.
            for key in ("structured_output", "result", "content", "response", "text"):
                value = envelope.get(key)
                if isinstance(value, str):
                    try:
                        return model.model_validate_json(value.strip())
                    except ValidationError:
                        continue
                if isinstance(value, dict):
                    try:
                        return model.model_validate(value)
                    except ValidationError:
                        continue

        raise RuntimeError(f"{provider} returned invalid JSON: {direct_error}")
