from typer.testing import CliRunner

from memory_mcp import cli


class FakeStore:
    def __init__(self, root):
        self.root = root

    def create_memory(self, memory):
        return memory.model_copy(
            update={
                "id": "mem_test",
                "content_for_embedding": "test",
            }
        )


def test_create_uses_situation_lesson_action_flags(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_store(root):
        captured["root"] = root
        store = FakeStore(root)
        original_create = store.create_memory

        def create_memory(memory):
            captured["memory"] = memory
            return original_create(memory)

        store.create_memory = create_memory
        return store

    monkeypatch.setattr(cli, "_store", fake_store)

    result = CliRunner().invoke(
        cli.app,
        [
            "create",
            "--situation",
            "When running tests in this repo.",
            "--lesson",
            "Direct pytest used the wrong environment.",
            "--action",
            "Use uv run pytest.",
            "--tag",
            "testing",
            "--root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert captured["memory"].when_useful == "When running tests in this repo."
    assert captured["memory"].what_happened == "Direct pytest used the wrong environment."
    assert captured["memory"].helpful_explanation == "Use uv run pytest."
    assert captured["memory"].tags == ["testing"]


def test_process_can_use_claude_extractor_options(monkeypatch, tmp_path) -> None:
    captured = {}

    class FakeExtractor:
        def __init__(self, **kwargs):
            captured["extractor_kwargs"] = kwargs

    class FakeResult:
        def to_dict(self):
            return {"ok": True}

    class FakeWorkflow:
        def __init__(self, *, root):
            captured["root"] = root

        def process(self, **kwargs):
            captured["process_kwargs"] = kwargs
            return FakeResult()

    monkeypatch.setattr(cli, "ClaudeCliExtractor", FakeExtractor)
    monkeypatch.setattr(cli, "OperatorWorkflow", FakeWorkflow)

    result = CliRunner().invoke(
        cli.app,
        [
            "process",
            "--root",
            str(tmp_path),
            "--extractor",
            "claude",
            "--claude-bin",
            "claude-test",
            "--model",
            "sonnet",
            "--effort",
            "high",
            "--timeout",
            "30",
            "--extraction-limit",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert '"ok": true' in result.output
    assert captured["root"] == tmp_path
    assert captured["extractor_kwargs"] == {
        "claude_bin": "claude-test",
        "model": "sonnet",
        "effort": "high",
        "timeout_seconds": 30,
        "use_project_context": False,
    }
    assert captured["process_kwargs"]["extractor"] is not None
    assert captured["process_kwargs"]["extraction_limit"] == 2
