from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from memory_mcp.core.events import EventRecord, SessionSegmentRecord

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


class MemoryExtractor(Protocol):
    def extract(
        self,
        *,
        segment: SessionSegmentRecord,
        events: list[EventRecord],
    ) -> ExtractionResult:
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
        with tempfile.TemporaryDirectory(prefix="memory-mcp-extract-") as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "memory_candidate.schema.json"
            output_path = temp_path / "codex-output.json"
            schema_path.write_text(
                json.dumps(ExtractionResult.model_json_schema(), indent=2),
                encoding="utf-8",
            )
            cmd = [
                self.codex_bin,
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
            if self.model is not None:
                cmd.extend(["--model", self.model])
            if self.effort is not None:
                cmd.extend(["--config", f'model_reasoning_effort="{self.effort}"'])
            if self.use_project_context and segment.project and Path(segment.project).exists():
                cmd.extend(["--cd", segment.project])
            cmd.append("-")

            try:
                completed = subprocess.run(
                    cmd,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    cwd=temp_path,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"codex exec timed out after {self.timeout_seconds}s"
                ) from exc

            if completed.returncode != 0:
                raise RuntimeError(
                    "codex exec failed "
                    f"(exit={completed.returncode}): {completed.stderr.strip()}"
                )
            if not output_path.exists():
                raise RuntimeError("codex exec did not write output-last-message file")

            raw_output = output_path.read_text(encoding="utf-8").strip()
            return _parse_extraction_output(raw_output, provider="codex exec")


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
        schema_json = json.dumps(ExtractionResult.model_json_schema())
        with tempfile.TemporaryDirectory(prefix="memory-mcp-extract-") as temp_dir:
            temp_path = Path(temp_dir)
            cmd = [
                self.claude_bin,
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
            if self.model is not None:
                cmd.extend(["--model", self.model])
            if self.effort is not None:
                cmd.extend(["--effort", self.effort])
            if self.use_project_context and segment.project and Path(segment.project).exists():
                cmd.extend(["--add-dir", segment.project])
            cmd.append(prompt)

            try:
                completed = subprocess.run(
                    cmd,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    cwd=temp_path,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"claude --print timed out after {self.timeout_seconds}s"
                ) from exc

            if completed.returncode != 0:
                raise RuntimeError(
                    "claude --print failed "
                    f"(exit={completed.returncode}): "
                    f"{completed.stderr.strip() or completed.stdout.strip()}"
                )

            return _parse_extraction_output(completed.stdout.strip(), provider="claude")


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


def _parse_extraction_output(raw_output: str, *, provider: str) -> ExtractionResult:
    try:
        return ExtractionResult.model_validate_json(raw_output)
    except ValidationError as direct_error:
        try:
            envelope = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{provider} returned invalid extraction JSON: {direct_error}"
            ) from exc

        if isinstance(envelope, dict):
            # `--output-format json` wraps the reply in an envelope; with
            # `--json-schema` the validated object lands in `structured_output`.
            for key in ("structured_output", "result", "content", "response", "text"):
                value = envelope.get(key)
                if isinstance(value, str):
                    try:
                        return ExtractionResult.model_validate_json(value.strip())
                    except ValidationError:
                        continue
                if isinstance(value, dict):
                    try:
                        return ExtractionResult.model_validate(value)
                    except ValidationError:
                        continue

        raise RuntimeError(
            f"{provider} returned invalid extraction JSON: {direct_error}"
        )
