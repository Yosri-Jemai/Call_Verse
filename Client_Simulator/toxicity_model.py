"""
Optional ML model for bad-words / toxicity detection (second layer on top of profanity.py).

    from toxicity_model import load_toxicity_model
    model = load_toxicity_model("auto")        # None if you do not want one
    model.predict("Vous ne servez à rien")     # {"score": 0.93, "toxic": True, "model": "..."}

Why a model on top of the word list: the list (profanity.py) is precise but only knows the words it
contains. A multilingual classifier also catches insults without a forbidden word ("vous êtes une
bonne à rien") and new spellings. The word list stays the authority (see judge.py): the model can only
ADD detections, and a detection made by the model alone is treated more gently (level 1-2).

Models (Hugging Face, loaded with `transformers`; first use downloads the weights):
  auto / default  textdetox/twitter-xlmr-toxicity-classifier   XLM-R large, ~560M params, 15 languages
                  (French and English F1 ~0.91, Arabic ~0.62, Arabizi / Darija not covered: weak there)
  alternatives    textdetox/xlmr-large-toxicity-classifier-v2  (newer, same family)
                  gorkem371/toxicity-classifier-xlmr-base-v5   XLM-R base, trained on Arabic dialects and
                                                               Arabizi; needs its own text normalisation,
                                                               check the model card before using it
Any text-classification model with a "toxic" label works: pass its name in TOXICITY_MODEL.

Environment: TOXICITY_MODEL (model name, or "none"), TOXICITY_THRESHOLD (default 0.7),
             TOXICITY_DEVICE (cpu | cuda | mps, default: automatic).
Install: pip install transformers torch
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_MODEL = "textdetox/twitter-xlmr-toxicity-classifier"
_CACHE: dict[tuple, "ToxicityModel"] = {}


class ToxicityModelError(RuntimeError):
    """The model cannot be loaded (missing package, no network, unknown name...)."""


class ToxicityModel:
    def __init__(self, name: str = DEFAULT_MODEL, threshold: float = 0.7, device: Optional[str] = None,
                 pipeline=None):
        """`pipeline` is for tests: any callable text -> [{"label": ..., "score": ...}] (all labels)."""
        self.name = name
        self.threshold = threshold
        self.last_error = ""
        self._pipe = pipeline
        if pipeline is None:
            try:
                from transformers import pipeline as hf_pipeline
            except ImportError as e:
                raise ToxicityModelError("transformers is not installed. Run: pip install transformers torch") from e
            try:
                kw = {"device": device} if device else {}
                self._pipe = hf_pipeline("text-classification", model=name, top_k=None, truncation=True,
                                         max_length=256, **kw)
            except Exception as e:                      # unknown model, offline, out of memory...
                raise ToxicityModelError(f"Cannot load '{name}': {type(e).__name__}: {e}") from e

    @staticmethod
    def _toxic_score(scores) -> float:
        """Probability of the toxic class, whatever the label names are."""
        if scores and isinstance(scores[0], list):      # batch output
            scores = scores[0]
        by = {str(s["label"]).lower(): float(s["score"]) for s in scores}
        for key in ("toxic", "toxicity", "label_1", "1", "offensive", "hate", "abusive"):
            if key in by:
                return by[key]
        if len(by) == 2 and any(k in by for k in ("neutral", "non-toxic", "not-toxic", "non_toxic", "label_0", "0")):
            return 1.0 - next(v for k, v in by.items() if k in ("neutral", "non-toxic", "not-toxic", "non_toxic",
                                                                   "label_0", "0"))
        raise ValueError(f"cannot find the toxic label in {sorted(by)}")

    def predict(self, text: str) -> Optional[dict]:
        """{"score", "toxic", "model"} or None when the model fails (never raises: it must not break a call)."""
        if not text or not text.strip():
            return {"score": 0.0, "toxic": False, "model": self.name}
        try:
            score = self._toxic_score(self._pipe(text))
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"[:300]
            return None
        return {"score": round(score, 4), "toxic": score >= self.threshold, "model": self.name}


def load_toxicity_model(name: Optional[str] = None) -> Optional[ToxicityModel]:
    """None / "none" -> no model. "auto" -> TOXICITY_MODEL from the environment, else the default.
    Raises ToxicityModelError when the model cannot start (no silent downgrade)."""
    if not name or name == "none":
        return None
    if name == "auto":
        name = os.getenv("TOXICITY_MODEL", DEFAULT_MODEL)
        if name == "none":
            return None
    threshold = float(os.getenv("TOXICITY_THRESHOLD", "0.7"))
    key = (name, threshold)
    if key not in _CACHE:                               # loading is slow (seconds, hundreds of MB): do it once
        _CACHE[key] = ToxicityModel(name, threshold, os.getenv("TOXICITY_DEVICE") or None)
    return _CACHE[key]
