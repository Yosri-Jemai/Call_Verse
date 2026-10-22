import argparse
import json
from datetime import date
from pathlib import Path

from src.tools.bank_policy_search import ensure_policy_table
from src.db.connection import get_connection

REQUIRED_FIELDS = {"category", "title", "content", "source"}
OPTIONAL_FIELDS = {"source_url", "valid_from", "valid_until"}


def _parse_date(value: str | None, field: str, line_number: int) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Line {line_number}: {field} must use YYYY-MM-DD format."
        ) from exc


def load_policy_documents(path: Path) -> list[tuple]:
    documents = []
    with path.open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Line {line_number}: invalid JSON.") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Line {line_number}: each JSONL record must be an object.")

            missing = REQUIRED_FIELDS - record.keys()
            unknown = record.keys() - REQUIRED_FIELDS - OPTIONAL_FIELDS
            if missing or unknown:
                details = []
                if missing:
                    details.append(f"missing fields: {', '.join(sorted(missing))}")
                if unknown:
                    details.append(f"unknown fields: {', '.join(sorted(unknown))}")
                raise ValueError(f"Line {line_number}: {'; '.join(details)}.")

            for field in ("category", "title", "content", "source"):
                if not isinstance(record[field], str) or not record[field].strip():
                    raise ValueError(f"Line {line_number}: {field} must be a non-empty string.")

            source_url = record.get("source_url")
            if source_url is not None and not isinstance(source_url, str):
                raise ValueError(f"Line {line_number}: source_url must be a string.")

            documents.append(
                (
                    record["category"].strip(),
                    record["title"].strip(),
                    record["content"].strip(),
                    record["source"].strip(),
                    source_url.strip() if source_url else None,
                    _parse_date(record.get("valid_from"), "valid_from", line_number),
                    _parse_date(record.get("valid_until"), "valid_until", line_number),
                )
            )

    if not documents:
        raise ValueError(f"No policy documents found in {path}.")
    return documents


def ingest_policy_documents(path: Path) -> int:
    documents = load_policy_documents(path)
    ensure_policy_table()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO bank_policy_documents
                    (category, title, content, source, source_url, valid_from, valid_until)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source, title) DO UPDATE SET
                    category = EXCLUDED.category,
                    content = EXCLUDED.content,
                    source_url = EXCLUDED.source_url,
                    valid_from = EXCLUDED.valid_from,
                    valid_until = EXCLUDED.valid_until,
                    is_active = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                """,
                documents,
            )
    return len(documents)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import official bank-policy documents from a UTF-8 JSONL file."
    )
    parser.add_argument("file", type=Path, help="JSONL file containing one policy per line")
    args = parser.parse_args()
    count = ingest_policy_documents(args.file)
    print(f"Imported or updated {count} bank-policy documents.")


if __name__ == "__main__":
    main()
