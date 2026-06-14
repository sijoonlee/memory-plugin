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
