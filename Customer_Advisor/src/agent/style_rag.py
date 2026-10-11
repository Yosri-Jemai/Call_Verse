import logging
import os
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger("cashy.agent")

STYLE_INDEX_PATH = Path(os.getenv("CASHY_STYLE_INDEX", "data/support_style.sqlite3"))
MAX_QUERY_TERMS = 12


def retrieve_style_examples(query: str, limit: int = 2) -> str:
    """Retrieve dialogue examples for response style, never as a factual source."""
    if not query.strip() or not STYLE_INDEX_PATH.is_file():
        return ""

    terms = list(dict.fromkeys(re.findall(r"\w+", query.lower())))[:MAX_QUERY_TERMS]
    if not terms:
        return ""

    match_query = " OR ".join(f'"{term}"' for term in terms)
    with sqlite3.connect(f"file:{STYLE_INDEX_PATH.as_posix()}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            """
            SELECT examples.client_question, examples.agent_answer
            FROM style_search
            JOIN style_examples AS examples ON examples.id = style_search.rowid
            WHERE style_search MATCH ?
            ORDER BY bm25(style_search)
            LIMIT ?
            """,
            (match_query, limit),
        ).fetchall()

    if not rows:
        return ""

    examples = "\n\n".join(
        f"Customer: {question[:350]}\nAgent: {answer[:800]}"
        for question, answer in rows
    )
    logger.debug("Retrieved %d customer-support style examples", len(rows))
    return (
        "The following retrieved synthetic banking-dialogue examples are for tone and "
        "conversation flow only. They are not bank policy or customer records. Never copy "
        "their names, account/card details, dates, amounts, or claims as facts. Answer using "
        "the current conversation and authorized tools only.\n\n"
        f"{examples}"
    )
