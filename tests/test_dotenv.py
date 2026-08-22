"""Loading .env, and the rule that the real environment wins."""

import os

from pipeline.config import load_dotenv


def test_values_are_loaded(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-test\nLTX_API_KEY=ltx-test\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LTX_API_KEY", raising=False)

    assert load_dotenv(env) == 2
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test"


def test_the_real_environment_is_never_overridden(tmp_path, monkeypatch):
    """A stale .env in a checkout must not shadow a CI secret."""
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-ci")

    load_dotenv(env)
    assert os.environ["ANTHROPIC_API_KEY"] == "from-ci"


def test_comments_blanks_quotes_and_export_are_handled(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        '# a comment\n\nexport A=1\nB="two"\nC=\'three\'\nnot a pair\nD=\n',
        encoding="utf-8",
    )
    for name in "ABCD":
        monkeypatch.delenv(name, raising=False)

    load_dotenv(env)
    assert os.environ["A"] == "1"
    assert os.environ["B"] == "two"
    assert os.environ["C"] == "three"
    assert os.environ["D"] == ""


def test_a_missing_file_is_not_an_error(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == 0
