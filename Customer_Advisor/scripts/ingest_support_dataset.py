import argparse
import logging
import sqlite3
from pathlib import Path

from datasets import load_dataset

logger = logging.getLogger("cashy.ingest")
DATASET_ID = "Yosriii/customer-support-client-agent-conversations"


def main():
    parser = argparse.ArgumentParser(
        description="Build a local SQLite full-text style-example index from the banking support dataset."
    )
    parser.add_argument("--num-examples", type=int, default=5000)
    parser.add_argument("--output", type=Path, default=Path("data/support_style.sqlite3"))
    parser.add_argument("--force", action="store_true", help="Replace an existing style index.")
    args = parser.parse_args()

    if args.num_examples < 1:
        parser.error("--num-examples must be greater than zero")
    if args.output.exists() and not args.force:
        parser.error(f"{args.output} already exists; pass --force to replace it")

    dataset = load_dataset(
        DATASET_ID,
        split=f"train[:{args.num_examples}]",
    )
    required_fields = {"client_question", "refined_agent_answer"}
    if not required_fields.issubset(dataset.column_names):
        raise ValueError(
            f"Dataset is missing required fields: {sorted(required_fields - set(dataset.column_names))}"
        )

    records = [
        (row["client_question"].strip(), row["refined_agent_answer"].strip())
        for row in dataset
        if isinstance(row["client_question"], str)
        and isinstance(row["refined_agent_answer"], str)
        and row["client_question"].strip()
        and row["refined_agent_answer"].strip()
    ]
    if not records:
        raise ValueError("No usable question-and-answer examples found in the selected dataset split")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.output) as conn:
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
            """
            CREATE VIRTUAL TABLE style_search USING fts5(
                client_question,
                agent_answer,
                tokenize='unicode61'
            )
            """
        )
        conn.executemany(
            "INSERT INTO style_examples (client_question, agent_answer) VALUES (?, ?)",
            records,
        )
        conn.executemany(
            """
            INSERT INTO style_search (rowid, client_question, agent_answer)
            VALUES (?, ?, ?)
            """,
            [(index, question, answer) for index, (question, answer) in enumerate(records, 1)],
        )

    logger.info("Indexed %d examples from %s into %s", len(records), DATASET_ID, args.output)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
