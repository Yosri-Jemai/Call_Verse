import argparse
import json
import logging
from pathlib import Path
from typing import Any

from src.agent.policy_rag import (
    COLLECTION_NAME,
    build_policy_chunks,
    get_chroma_client,
    get_embeddings,
    get_policy_collection,
    is_sensitive_policy_text,
    parse_policy_date,
)

logger = logging.getLogger("cashy.ingest")

REQUIRED_FIELDS = {"category", "title", "content", "source"}
OPTIONAL_FIELDS = {"source_url", "valid_from", "valid_until"}


def find_jsonl_files(inputs: list[Path]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    errors: list[str] = []
    for path in inputs:
        if path.is_dir():
            candidates = sorted(path.rglob("*.jsonl"))
            if not candidates:
                errors.append(f"{path}: aucun fichier .jsonl trouvé.")
            files.extend(candidates)
        elif path.is_file():
            files.append(path)
        else:
            errors.append(f"{path}: chemin introuvable.")
    return list(dict.fromkeys(files)), errors


def load_policy_documents(
    files: list[Path],
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{path}: lecture impossible ({exc}).")
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            location = f"{path}:{line_number}"
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("la ligne JSONL doit contenir un objet")
                missing = REQUIRED_FIELDS - record.keys()
                unknown = record.keys() - REQUIRED_FIELDS - OPTIONAL_FIELDS
                if missing or unknown:
                    details = []
                    if missing:
                        details.append(f"champs requis absents: {', '.join(sorted(missing))}")
                    if unknown:
                        details.append(f"champs inconnus: {', '.join(sorted(unknown))}")
                    raise ValueError("; ".join(details))

                for field in ("category", "title", "content", "source"):
                    if not isinstance(record[field], str) or not record[field].strip():
                        raise ValueError(f"{field} doit être une chaîne non vide")
                source_url = record.get("source_url")
                if source_url is not None and not isinstance(source_url, str):
                    raise ValueError("source_url doit être une chaîne ou null")

                valid_from = parse_policy_date(record.get("valid_from"), "valid_from")
                valid_until = parse_policy_date(record.get("valid_until"), "valid_until")
                if valid_from and valid_until and valid_from > valid_until:
                    raise ValueError("valid_from doit précéder ou égaler valid_until")
                if is_sensitive_policy_text(json.dumps(record, ensure_ascii=False)):
                    raise ValueError(
                        "contenu sensible détecté; la ligne n'a pas été importée"
                    )

                records.append(
                    {
                        "category": record["category"].strip(),
                        "title": record["title"].strip(),
                        "content": record["content"].strip(),
                        "source": record["source"].strip(),
                        "source_url": source_url.strip() if source_url else None,
                        "valid_from": valid_from,
                        "valid_until": valid_until,
                    }
                )
            except (json.JSONDecodeError, ValueError) as exc:
                errors.append(f"{location}: {exc}.")
    return records, errors


def _remove_stale_chunks(collection: Any, chunks: list[dict[str, Any]]) -> None:
    expected_ids_by_document: dict[tuple[str, str], set[str]] = {}
    for chunk in chunks:
        metadata = chunk["metadata"]
        key = metadata["source"], metadata["title"]
        expected_ids_by_document.setdefault(key, set()).add(chunk["id"])

    for source, title in expected_ids_by_document:
        existing = collection.get(
            where={
                "$and": [
                    {"source": {"$eq": source}},
                    {"title": {"$eq": title}},
                ]
            },
            include=["metadatas"],
        )
        expected_ids = expected_ids_by_document[(source, title)]
        stale_ids = [identifier for identifier in existing["ids"] if identifier not in expected_ids]
        if stale_ids:
            collection.delete(ids=stale_ids)


def ingest_policy_records(
    collection: Any,
    records: list[dict[str, Any]],
    embeddings: Any,
) -> tuple[int, int]:
    chunks = build_policy_chunks(records)
    _remove_stale_chunks(collection, chunks)
    vectors = embeddings.embed_documents([chunk["embedding_text"] for chunk in chunks])
    if chunks:
        collection.upsert(
            ids=[chunk["id"] for chunk in chunks],
            documents=[chunk["text"] for chunk in chunks],
            metadatas=[chunk["metadata"] for chunk in chunks],
            embeddings=vectors,
        )
    chunk_count = len(chunks)
    return len(records), chunk_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importer des politiques bancaires JSONL dans ChromaDB."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Un ou plusieurs fichiers JSONL ou dossiers à parcourir récursivement",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Vider la collection bank_policies avant l'import",
    )
    args = parser.parse_args()

    files, discovery_errors = find_jsonl_files(args.paths)
    for error in discovery_errors:
        logger.error("%s", error)
    records, line_errors = load_policy_documents(files)
    for error in line_errors:
        logger.error("%s", error)
    if not records:
        parser.error("Aucun document valide à importer; la collection n'a pas été modifiée.")

    chunks = build_policy_chunks(records)
    embeddings = get_embeddings()
    vectors = embeddings.embed_documents([chunk["embedding_text"] for chunk in chunks])

    client = get_chroma_client()
    collection = get_policy_collection(client)
    if args.reset:
        client.delete_collection(COLLECTION_NAME)
        collection = get_policy_collection(client)
    _remove_stale_chunks(collection, chunks)
    if chunks:
        collection.upsert(
            ids=[chunk["id"] for chunk in chunks],
            documents=[chunk["text"] for chunk in chunks],
            metadatas=[chunk["metadata"] for chunk in chunks],
            embeddings=vectors,
        )

    print(
        f"Documents importés/mis à jour: {len(records)}; chunks importés/mis à jour: {len(chunks)}."
    )
    print(f"Lignes ignorées: {len(line_errors)}; collection: {COLLECTION_NAME}.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
