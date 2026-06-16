"""
Placeholder generator for masking sensitive data.

Produces **type-preserving, LLM-legible** surrogate tokens so that downstream
models can tell what kind of field each mask stands for (and process it
accordingly) instead of discarding it as noise.

Token format
------------
``{{<TYPE>_<NNNNNN>:<shape>}}``

* ``TYPE``      — the entity type (PHONE, EMAIL, IDCN, SECRET, …). Names and
  organizations carry no useful shape, so they use ``{{PERSON_000001}}`` with no
  ``:shape`` suffix.
* ``NNNNNN``    — a per-type counter, guaranteeing uniqueness across entities of
  the same type. Final uniqueness/stability is still enforced by the vault.
* ``shape``     — a **fully redacted** structural template of the original
  (digit -> ``#``, ASCII letter -> ``X``, CJK -> ``某``; separators such as
  ``- @ . / :`` are kept because they are structural, not identifying). No
  original character survives, so the shape leaks nothing — it only tells the
  downstream LLM "this is a phone-shaped value" / "an 18-digit ID" / etc.

The whole token string is the vault key, so masking is losslessly reversible:
``{{PHONE_000001:###########}}`` -> original phone.
"""

from typing import Dict
from ..core.span import EntityType


# Types whose mask carries no shape suffix (names/orgs have no meaningful shape,
# and the bare ``{{PERSON_000001}}`` form is depended on by the exec wrapper and
# several tests).
_NO_SHAPE_TYPES = {"PERSON", "ORG"}

# Friendlier token names for some EntityType values.
_TYPE_ALIASES = {
    "DATE_TIME": "DATE",
    "ACCOUNT_NUMBER": "ACCOUNT",
    "CREDIT_CARD": "CARD",
}

_SHAPE_CAP = 32  # cap shape length so addresses/long secrets don't bloat the token


class PlaceholderGenerator:
    """Generates unique, stable, type-preserving placeholders.

    Design:
    1. Same (original, type) -> same token (stable within a run; the vault
       enforces cross-run stability and resolves collisions).
    2. Every token names its type so a downstream LLM knows the field kind.
    3. Structured types also carry a redacted shape; the original never leaks.
    """

    def __init__(self) -> None:
        self._counters: Dict[str, int] = {}
        self._cache: Dict[tuple, str] = {}

    def generate(self, original: str, entity_type: EntityType) -> str:
        """Return the surrogate token for ``original`` of type ``entity_type``."""
        cache_key = (original, entity_type)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        type_name = _token_type_name(entity_type)
        n = self._counters.get(type_name, 0) + 1
        self._counters[type_name] = n

        if type_name in _NO_SHAPE_TYPES:
            token = f"{{{{{type_name}_{n:06d}}}}}"
        else:
            shape = _redact_shape(original)
            token = f"{{{{{type_name}_{n:06d}:{shape}}}}}" if shape else f"{{{{{type_name}_{n:06d}}}}}"

        self._cache[cache_key] = token
        return token

    def get_reverse_mappings(self) -> Dict[str, str]:
        """Return ``{masked -> original}`` for everything generated so far."""
        return {v: k[0] for k, v in self._cache.items()}

    def clear_cache(self) -> None:
        """Reset counters and cache (start fresh)."""
        self._cache.clear()
        self._counters = {}


def _token_type_name(entity_type: EntityType) -> str:
    return _TYPE_ALIASES.get(entity_type.value, entity_type.value)


def _redact_shape(text: str) -> str:
    """Build a fully-redacted structural template of ``text``.

    Digits -> ``#``, ASCII letters -> ``X``, CJK -> ``某``. Separators and
    punctuation are preserved (structural, non-identifying). No original
    character survives. Capped at ``_SHAPE_CAP`` chars.
    """
    out = []
    for ch in text:
        if ch.isdigit():
            out.append("#")
        elif ch.isascii() and ch.isalpha():
            out.append("X")
        elif "一" <= ch <= "鿿":
            out.append("某")
        else:
            out.append(ch)
    shape = "".join(out)
    if len(shape) > _SHAPE_CAP:
        shape = shape[: _SHAPE_CAP - 1] + "…"
    return shape
