# test_groq.py
import os
import time
from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()  # lit .env

key = os.getenv("GROQ_API_KEY")
model = os.getenv("MODEL_NAME") or "openai/gpt-oss-120b"

print("Provider :", os.getenv("LLM_PROVIDER"))
print("Modèle   :", model)
print("Clé Groq trouvée :", bool(key))  # True/False, jamais la clé

if not key:
    raise SystemExit("GROQ_API_KEY manquante dans .env")

llm = ChatGroq(
    model=model,
    api_key=key,
    max_retries=0,   # affiche l'erreur 429 au lieu d'attendre en silence
    max_tokens=2048, # large, car gpt-oss réfléchit avant de répondre
)

for i in range(3):
    t = time.perf_counter()
    try:
        r = llm.invoke("Dis bonjour en une phrase.")
        print(f"essai {i + 1}: {time.perf_counter() - t:.1f}s")
        print("  réponse :", r.content)
        print("  tokens  :", r.usage_metadata)
    except Exception as e:
        print(f"essai {i + 1}: ERREUR après {time.perf_counter() - t:.1f}s")
        print("  ->", str(e)[:300])