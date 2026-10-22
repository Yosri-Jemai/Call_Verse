import hashlib
import json
import math
import re
from datetime import date

import chromadb

from scripts.ingest_bank_policies_chroma import (
    find_jsonl_files,
    ingest_policy_records,
    load_policy_documents,
)
from src.agent.policy_rag import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLLECTION_NAME,
    E5Embeddings,
    NO_RELEVANT_DOCUMENTS,
    build_policy_chunks,
    build_policy_filter,
    is_sensitive_policy_text,
    search_policy_collection,
    split_policy_text,
)


class FakeSentenceTransformer:
    def __init__(self):
        self.inputs = []

    def encode(self, texts, normalize_embeddings=True):
        self.inputs.extend(texts)
        vectors = []
        for text in texts:
            vector = [0.0] * 96
            for token in re.findall(r"[a-zà-ÿ0-9]+", text.lower()):
                index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % len(vector)
                vector[index] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector] if normalize_embeddings else vector)
        return vectors


def make_record(
    category,
    title,
    content,
    *,
    source=None,
    valid_from=None,
    valid_until=None,
):
    return {
        "category": category,
        "title": title,
        "content": content,
        "source": source or f"Fiche officielle : {title}",
        "source_url": None,
        "valid_from": valid_from,
        "valid_until": valid_until,
    }


def make_three_documents():
    return [
        make_record(
            "fees",
            "Frais mensuels",
            "Frais mensuels du compte courant trois dinars par mois.",
            valid_from=date(2026, 1, 1),
        ),
        make_record(
            "limits",
            "Plafond retrait carte",
            "Le plafond de retrait est deux cents dinars par jour.",
            valid_from=date(2027, 1, 1),
        ),
        make_record(
            "procedures",
            "Remplacement carte",
            "Pour remplacer une carte perdue, contacter une agence.",
            valid_until=date(2020, 1, 1),
        ),
    ]


class InMemoryCollection:
    def __init__(self):
        self.items = {}
        self.query_arguments = None

    def get(self, where, include):
        source = where["$and"][0]["source"]["$eq"]
        title = where["$and"][1]["title"]["$eq"]
        identifiers = [
            identifier
            for identifier, item in self.items.items()
            if item["metadata"]["source"] == source
            and item["metadata"]["title"] == title
        ]
        return {"ids": identifiers}

    def delete(self, ids):
        for identifier in ids:
            self.items.pop(identifier, None)

    def upsert(self, ids, documents, metadatas, embeddings):
        for identifier, document, metadata, embedding in zip(
            ids, documents, metadatas, embeddings
        ):
            self.items[identifier] = {
                "document": document,
                "metadata": metadata,
                "embedding": embedding,
            }

    def query(self, **kwargs):
        self.query_arguments = kwargs
        return {
            "documents": [["Frais du compte."]],
            "metadatas": [
                [
                    {
                        "category": "fees",
                        "title": "Tarif",
                        "source": "Tarif officiel",
                        "source_url": "",
                        "valid_from": 0,
                        "valid_until": 99_991_231,
                    }
                ]
            ],
            "distances": [[0.6]],
        }

    def count(self):
        return len(self.items)


def test_chunking_preserves_short_text_and_overlaps_long_text():
    short_text = "Une règle courte."
    long_text = " ".join(f"condition{i}" for i in range(180))

    assert split_policy_text(short_text) == [short_text]
    chunks = split_policy_text(long_text)
    assert len(chunks) > 1
    assert all(len(chunk) <= CHUNK_SIZE for chunk in chunks)
    assert chunks[0][-60:] in chunks[1]
    assert CHUNK_OVERLAP == 80


def test_e5_embeddings_apply_distinct_passage_and_query_prefixes():
    model = FakeSentenceTransformer()
    embeddings = E5Embeddings(model)

    embeddings.embed_documents(["texte de règle"])
    embeddings.embed_query("question utilisateur")

    assert model.inputs == ["passage: texte de règle", "query: question utilisateur"]


def test_chunks_include_integer_validity_and_required_metadata():
    record = make_record("fees", "Tarif", "Frais du compte.")
    chunk = build_policy_chunks([record])[0]

    assert chunk["metadata"]["valid_from"] == 0
    assert chunk["metadata"]["valid_until"] == 99_991_231
    assert chunk["metadata"]["chunk_index"] == 0
    assert {
        "category",
        "title",
        "source",
        "source_url",
        "valid_from",
        "valid_until",
        "chunk_index",
    } == set(chunk["metadata"])
    assert len(chunk["id"]) == 64


def test_date_filter_uses_integer_dates_and_optional_category():
    filters = build_policy_filter(date(2026, 10, 15), category="fees")

    assert filters == {
        "$and": [
            {"valid_from": {"$lte": 20261015}},
            {"valid_until": {"$gte": 20261015}},
            {"category": {"$eq": "fees"}},
        ]
    }
    assert build_policy_filter(date(2026, 10, 15))["$and"][-1] == {
        "valid_until": {"$gte": 20261015}
    }


def test_import_loader_accepts_utf8_jsonl_and_reports_invalid_lines(tmp_path):
    policy_file = tmp_path / "policies.jsonl"
    policy_file.write_text(
        json.dumps(
            {
                "category": "frais",
                "title": "Frais de tenue",
                "content": "La tenue coûte trois dinars.",
                "source": "Tarif officiel",
                "valid_from": "2026-01-01",
            },
            ensure_ascii=False,
        )
        + "\nnot-json\n",
        encoding="utf-8",
    )

    records, errors = load_policy_documents([policy_file])

    assert len(records) == 1
    assert records[0]["content"] == "La tenue coûte trois dinars."
    assert records[0]["valid_from"] == date(2026, 1, 1)
    assert len(errors) == 1
    assert ":2:" in errors[0]


def test_import_skips_sensitive_card_credentials(tmp_path):
    policy_file = tmp_path / "sensitive.jsonl"
    policy_file.write_text(
        json.dumps(
            {
                "category": "procedures",
                "title": "Carte",
                "content": "Numéro de carte: 4111111111111111",
                "source": "Document interne",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    records, errors = load_policy_documents([policy_file])

    assert records == []
    assert "contenu sensible" in errors[0]
    assert is_sensitive_policy_text("PIN: 1234")


def test_directory_discovery_recurses_only_for_jsonl(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "policy.jsonl").write_text("", encoding="utf-8")
    (nested / "ignore.txt").write_text("not policy data", encoding="utf-8")

    files, errors = find_jsonl_files([tmp_path])

    assert files == [nested / "policy.jsonl"]
    assert errors == []


def test_import_is_idempotent_with_upsert_and_stale_chunk_cleanup():
    collection = InMemoryCollection()
    embeddings = E5Embeddings(FakeSentenceTransformer())
    long_record = make_record(
        "procedures",
        "Long procedure",
        " ".join(f"étape{i}" for i in range(200)),
    )

    ingest_policy_records(collection, [long_record], embeddings)
    first_count = collection.count()
    ingest_policy_records(collection, [long_record], embeddings)
    assert collection.count() == first_count

    shorter = {**long_record, "content": "Contacter une agence."}
    ingest_policy_records(collection, [shorter], embeddings)
    assert collection.count() == 1


def test_similarity_threshold_returns_no_relevant_document():
    collection = InMemoryCollection()

    result = search_policy_collection(
        "question sans rapport",
        collection,
        E5Embeddings(FakeSentenceTransformer()),
        threshold=0.55,
    )

    assert result["found"] is False
    assert result["message"] == NO_RELEVANT_DOCUMENTS
    assert collection.query_arguments["n_results"] == 4


def test_chroma_integration_date_filter_threshold_and_idempotent_upsert(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    embeddings = E5Embeddings(FakeSentenceTransformer())
    records = make_three_documents()

    imported_documents, imported_chunks = ingest_policy_records(
        collection, records, embeddings
    )
    assert (imported_documents, imported_chunks) == (3, 3)
    assert collection.metadata["hnsw:space"] == "cosine"
    assert collection.count() == 3

    ingest_policy_records(collection, records, embeddings)
    assert collection.count() == 3

    result = search_policy_collection(
        "frais mensuels compte courant",
        collection,
        embeddings,
        threshold=0.45,
        today=date(2026, 10, 15),
    )
    assert result["found"] is True
    assert len(result["documents"]) == 1
    assert result["documents"][0]["category"] == "fees"
    assert result["documents"][0]["source"] == "Fiche officielle : Frais mensuels"
    assert result["documents"][0]["valid_from"] == "2026-01-01"
    assert result["documents"][0]["similarity_score"] >= 0.45

    unrelated = search_policy_collection(
        "assurance habitation prêt immobilier",
        collection,
        embeddings,
        threshold=0.8,
        today=date(2026, 10, 15),
    )
    assert unrelated == {
        "found": False,
        "message": NO_RELEVANT_DOCUMENTS,
        "similarity_threshold": 0.8,
    }

    expired_and_future = search_policy_collection(
        "plafond retrait carte",
        collection,
        embeddings,
        category="limits",
        threshold=0.1,
        today=date(2026, 10, 15),
    )
    assert expired_and_future["found"] is False


def test_reimport_removes_stale_chunks_after_document_shrinks(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    embeddings = E5Embeddings(FakeSentenceTransformer())
    long_record = make_record(
        "procedures",
        "Long procedure",
        " ".join(f"étape{i}" for i in range(200)),
    )
    ingest_policy_records(collection, [long_record], embeddings)
    assert collection.count() > 1

    short_record = {**long_record, "content": "Contacter une agence."}
    ingest_policy_records(collection, [short_record], embeddings)
    assert collection.count() == 1
