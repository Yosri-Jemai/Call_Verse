import sqlite3

from src.agent import style_rag


def create_index(path):
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE style_examples (
                id INTEGER PRIMARY KEY,
                client_question TEXT NOT NULL,
                agent_answer TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE VIRTUAL TABLE style_search USING fts5(client_question, agent_answer)"
        )
        conn.execute(
            "INSERT INTO style_examples VALUES (1, ?, ?)",
            ("My card was declined", "I understand this is frustrating. Let's look into it."),
        )
        conn.execute(
            "INSERT INTO style_search (rowid, client_question, agent_answer) VALUES (1, ?, ?)",
            ("My card was declined", "I understand this is frustrating. Let's look into it."),
        )


def test_retrieves_examples_as_style_only_context(tmp_path, monkeypatch):
    index_path = tmp_path / "style.sqlite3"
    create_index(index_path)
    monkeypatch.setattr(style_rag, "STYLE_INDEX_PATH", index_path)

    result = style_rag.retrieve_style_examples("My card was declined")

    assert "tone and conversation flow only" in result
    assert "My card was declined" in result
    assert "Let's look into it." in result


def test_missing_index_disables_retrieval(tmp_path, monkeypatch):
    monkeypatch.setattr(style_rag, "STYLE_INDEX_PATH", tmp_path / "missing.sqlite3")

    assert style_rag.retrieve_style_examples("Hello") == ""
