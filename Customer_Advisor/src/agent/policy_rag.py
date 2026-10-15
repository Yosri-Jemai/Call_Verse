import hashlib
import logging
import re
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any

from src.config import settings

logger = logging.getLogger("cashy.agent")

COLLECTION_NAME = "bank_policies"
TOP_K = 4
CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
NO_RELEVANT_DOCUMENTS = "Aucun document pertinent trouvé."
CHROMA_PATH = Path(__file__).resolve().parents[2] / "data" / "chroma"
NO_VALID_FROM_DATE = 0
NO_VALID_UNTIL_DATE = 99_991_231

_CARD_NUMBER_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \b(?:password|passwd|mot\s+de\s+passe|pin|code\s+pin|cvv|cvc|
       security\s+code|otp|one[- ]time\s+(?:password|code)|code\s+à\s+usage\s+unique|
       verification\s+code|code\s+de\s+vérification)\b
    ["']?\s*[:=]\s*["']?([^\s"',;]+)
    """
)
_WHITESPACE_RE = re.compile(r"\s+")
_MODEL_LOCK = Lock()
_MODEL_NAME: str | None = None
_MODEL: Any | None = None
_CHROMA_CLIENT: Any | None = None
_CHROMA_LOCK = Lock()


class E5Embeddings:
    def __init__(self, model: Any | None = None):
        self.model = model if model is not None else _get_sentence_transformer()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode([f"passage: {text}" for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._encode([f"query: {text}"])[0]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, normalize_embeddings=True)
        return [[float(value) for value in vector] for vector in vectors]


def _get_sentence_transformer() -> Any:
    global _MODEL, _MODEL_NAME
    model_name = settings.embedding_model
    with _MODEL_LOCK:
        if _MODEL is None or _MODEL_NAME != model_name:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "Policy RAG dependencies are missing. Install them with "
                    r'.\.venv\Scripts\python.exe -m pip install -e ".[rag]".'
                ) from exc
            _MODEL = SentenceTransformer(model_name)
            _MODEL_NAME = model_name
            logger.info("Loaded policy embedding model: %s", model_name)
        return _MODEL


def get_embeddings() -> E5Embeddings:
    return E5Embeddings()


def get_chroma_client() -> Any:
    global _CHROMA_CLIENT
    with _CHROMA_LOCK:
        if _CHROMA_CLIENT is None:
            try:
                import chromadb
            except ImportError as exc:
                raise RuntimeError(
                    "ChromaDB is missing. Install RAG dependencies with "
                    r'.\.venv\Scripts\python.exe -m pip install -e ".[rag]".'
                ) from exc
            CHROMA_PATH.mkdir(parents=True, exist_ok=True)
            _CHROMA_CLIENT = chromadb.PersistentClient(path=str(CHROMA_PATH))
        return _CHROMA_CLIENT


def get_policy_collection(client: Any | None = None) -> Any:
    active_client = client if client is not None else get_chroma_client()
    return active_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def split_policy_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    normalized = _WHITESPACE_RE.sub(" ", text).strip()
    if not normalized:
        return []
    if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk size must be positive and overlap smaller than chunk size.")

    chunks = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start, end)
            if boundary > start + chunk_size // 2:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(normalized):
            break
        start = max(start + 1, end - overlap)
    return chunks


def is_sensitive_policy_text(text: str) -> bool:
    if _SECRET_ASSIGNMENT_RE.search(text):
        return True
    return any(
        _passes_luhn(match.group().replace(" ", "").replace("-", ""))
        for match in _CARD_NUMBER_RE.finditer(text)
    )


def _passes_luhn(number: str) -> bool:
    digits = [int(digit) for digit in number]
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def parse_policy_date(value: str | None, field: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must use YYYY-MM-DD format.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD format.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must use YYYY-MM-DD format.")
    return parsed


def policy_date_integer(value: date | None, absent_value: int) -> int:
    return int(value.strftime("%Y%m%d")) if value is not None else absent_value


def build_policy_chunks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks_by_id = {}
    for record in records:
        valid_from = policy_date_integer(record["valid_from"], NO_VALID_FROM_DATE)
        valid_until = policy_date_integer(record["valid_until"], NO_VALID_UNTIL_DATE)
        for chunk_index, text in enumerate(split_policy_text(record["content"])):
            identifier = hashlib.sha256(
                f"{record['source']}\0{record['title']}\0{chunk_index}".encode("utf-8")
            ).hexdigest()
            chunks_by_id[identifier] = {
                "id": identifier,
                "text": text,
                "embedding_text": f"{record['title']}\n{text}",
                "metadata": {
                    "category": record["category"],
                    "title": record["title"],
                    "source": record["source"],
                    "source_url": record["source_url"] or "",
                    "valid_from": valid_from,
                    "valid_until": valid_until,
                    "chunk_index": chunk_index,
                },
            }
    return list(chunks_by_id.values())


def upsert_policy_chunks(
    collection: Any,
    chunks: list[dict[str, Any]],
    embeddings: E5Embeddings,
) -> int:
    if not chunks:
        return 0
    vectors = embeddings.embed_documents([chunk["embedding_text"] for chunk in chunks])
    collection.upsert(
        ids=[chunk["id"] for chunk in chunks],
        documents=[chunk["text"] for chunk in chunks],
        metadatas=[chunk["metadata"] for chunk in chunks],
        embeddings=vectors,
    )
    return len(chunks)


def build_policy_filter(
    today: date | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    current_date = today or date.today()
    filters: list[dict[str, Any]] = [
        {"valid_from": {"$lte": policy_date_integer(current_date, NO_VALID_FROM_DATE)}},
        {"valid_until": {"$gte": policy_date_integer(current_date, NO_VALID_UNTIL_DATE)}},
    ]
    if category:
        filters.append({"category": {"$eq": category}})
    return {"$and": filters}


def search_policy_collection(
    query: str,
    collection: Any,
    embeddings: E5Embeddings,
    *,
    category: str | None = None,
    top_k: int = TOP_K,
    threshold: float | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    if not query.strip():
        return {"found": False, "message": NO_RELEVANT_DOCUMENTS}

    active_threshold = settings.similarity_threshold if threshold is None else threshold
    results = collection.query(
        query_embeddings=[embeddings.embed_query(query)],
        n_results=top_k,
        where=build_policy_filter(today=today, category=category),
        include=["documents", "metadatas", "distances"],
    )
    documents = []
    for text, metadata, distance in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        score = 1.0 - float(distance)
        if score < active_threshold:
            continue
        documents.append(
            {
                "text": text,
                "source": metadata["source"],
                "category": metadata["category"],
                "title": metadata["title"],
                "source_url": metadata.get("source_url") or None,
                "valid_from": _date_from_metadata(metadata["valid_from"]),
                "valid_until": _date_from_metadata(metadata["valid_until"]),
                "similarity_score": round(score, 4),
            }
        )
    if not documents:
        return {
            "found": False,
            "message": NO_RELEVANT_DOCUMENTS,
            "similarity_threshold": active_threshold,
        }
    return {
        "found": True,
        "similarity_threshold": active_threshold,
        "documents": documents,
    }


def _date_from_metadata(value: int) -> str | None:
    if value in (NO_VALID_FROM_DATE, NO_VALID_UNTIL_DATE):
        return None
    value_text = str(value)
    return f"{value_text[:4]}-{value_text[4:6]}-{value_text[6:]}"
