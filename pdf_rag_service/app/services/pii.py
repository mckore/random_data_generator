"""PII detection, tokenization, and redaction.

Two classes of PII are handled, decided per entity type (see config):

* Tokenized (reversible): each distinct value in a document is replaced with a
  stable per-document token such as ``<PERSON_1>``. The token -> value mapping is
  kept in a per-document vault (see ``services/vault.py``) and can be rendered
  back at runtime by authorised callers.
* Redacted (irreversible): replaced with a bare ``<US_SSN>``-style tag. The
  value is not stored anywhere by this service.

Detection uses Microsoft Presidio (spaCy NER + pattern recognizers) for most
entity types and ``pyap`` for full US street addresses, which Presidio's
LOCATION recognizer does not reliably capture.
"""

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from config import settings

logger = logging.getLogger(__name__)

ADDRESS_TYPE = "ADDRESS"
TOKEN_RE = re.compile(r"<([A-Z][A-Z_]*?)_(\d+)>")

# Short label used inside tokens for readability (<PHONE_1> rather than
# <PHONE_NUMBER_1>). Anything not listed uses the entity type verbatim.
_TOKEN_LABELS = {"PHONE_NUMBER": "PHONE"}
_LABEL_TO_TYPE = {v: k for k, v in _TOKEN_LABELS.items()}

# When spans overlap, higher priority wins. Addresses beat everything because
# Presidio frequently mis-labels fragments of an address (street numbers as
# DATE_TIME/PHONE_NUMBER, city as LOCATION).
_PRIORITY = {ADDRESS_TYPE: 100, "CREDIT_CARD": 90, "US_SSN": 90, "EMAIL_ADDRESS": 80}


@dataclass
class Span:
    start: int
    end: int
    entity_type: str
    text: str
    score: float

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class TokenizedPage:
    page: int
    text: str
    spans: List[Span] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def get_analyzer():
    """Build the Presidio analyzer once per process.

    Imported lazily so that modules/tests that never analyze text don't pay
    for loading spaCy.
    """
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": settings.spacy_model}],
        }
    )
    return AnalyzerEngine(nlp_engine=provider.create_engine(), supported_languages=["en"])


def _presidio_spans(text: str) -> List[Span]:
    entities = settings.presidio_entities
    if not entities or not text.strip():
        return []
    results = get_analyzer().analyze(
        text=text,
        language="en",
        entities=entities,
        score_threshold=settings.pii_score_threshold,
    )
    return [
        Span(r.start, r.end, r.entity_type, text[r.start : r.end], float(r.score))
        for r in results
    ]


def _locate(text: str, needle: str, cursor: int) -> Optional[Tuple[int, int]]:
    """Find ``needle`` in ``text`` at/after ``cursor``, tolerant of whitespace
    differences (pyap normalises whitespace and punctuation)."""
    parts = needle.split()
    if not parts:
        return None
    pattern = r"\s+".join(re.escape(p) for p in parts)
    match = re.compile(pattern, re.IGNORECASE).search(text, cursor)
    return (match.start(), match.end()) if match else None


def _address_spans(text: str) -> List[Span]:
    if ADDRESS_TYPE not in settings.tokenize_entities + settings.redact_entities:
        return []
    import pyap

    spans: List[Span] = []
    cursor = 0
    for addr in pyap.parse(text, country="US"):
        # pyap's match_start/match_end refer to a normalised copy of the text
        # and are not reliable offsets into ``text``; re-locate the address.
        located = _locate(text, addr.full_address, cursor)
        if located is None:
            logger.debug("pyap address could not be located in source text; skipping")
            continue
        start, end = located
        spans.append(Span(start, end, ADDRESS_TYPE, text[start:end], 1.0))
        cursor = end
    return spans


def _resolve_overlaps(spans: List[Span]) -> List[Span]:
    """Greedy non-overlapping selection: priority, then score, then length."""
    ordered = sorted(
        spans,
        key=lambda s: (-_PRIORITY.get(s.entity_type, 0), -s.score, -s.length, s.start),
    )
    chosen: List[Span] = []
    for span in ordered:
        if all(span.end <= c.start or span.start >= c.end for c in chosen):
            chosen.append(span)
    return sorted(chosen, key=lambda s: s.start)


def detect_spans(text: str) -> List[Span]:
    """All PII spans in ``text`` (non-overlapping, ordered by position)."""
    return _resolve_overlaps(_presidio_spans(text) + _address_spans(text))


# --------------------------------------------------------------------------- #
# Tokenization
# --------------------------------------------------------------------------- #


def _normalize(entity_type: str, value: str) -> str:
    """Key used to decide whether two spans refer to the same value."""
    if entity_type == "PHONE_NUMBER":
        return re.sub(r"\D", "", value)
    collapsed = re.sub(r"\s+", " ", value).strip().casefold()
    if entity_type == ADDRESS_TYPE:
        collapsed = re.sub(r"[.,]", "", collapsed)
    return collapsed


def token_label(entity_type: str) -> str:
    return _TOKEN_LABELS.get(entity_type, entity_type)


def token_type(label: str) -> str:
    return _LABEL_TO_TYPE.get(label, label)


class DocumentTokenizer:
    """Stateful per-document tokenizer.

    Feed pages in order via :meth:`process_page`; afterwards ``vault``,
    ``token_stats`` and ``inventory`` describe the whole document.
    """

    def __init__(self, rewrite_text: Optional[bool] = None):
        self.rewrite_text = settings.pii_mode == "tokenize" if rewrite_text is None else rewrite_text
        self._tokenize_types = set(settings.tokenize_entities)
        self._redact_types = set(settings.redact_entities)
        self._counters: Dict[str, int] = {}
        self._value_index: Dict[Tuple[str, str], str] = {}
        # token -> {"type": ..., "value": ...}
        self.vault: Dict[str, Dict[str, str]] = {}
        # token -> {"type": ..., "count": n, "first_page": p}
        self.token_stats: Dict[str, Dict[str, object]] = {}
        # entity_type -> {"count": n, "pages": [..]}
        self.inventory: Dict[str, Dict[str, object]] = {}

    # -- helpers -----------------------------------------------------------

    def _token_for(self, span: Span, page: int) -> str:
        key = (span.entity_type, _normalize(span.entity_type, span.text))
        token = self._value_index.get(key)
        if token is None:
            n = self._counters.get(span.entity_type, 0) + 1
            self._counters[span.entity_type] = n
            token = f"<{token_label(span.entity_type)}_{n}>"
            self._value_index[key] = token
            self.vault[token] = {"type": span.entity_type, "value": span.text}
            self.token_stats[token] = {"type": span.entity_type, "count": 0, "first_page": page}
        self.token_stats[token]["count"] += 1
        return token

    def _record_inventory(self, span: Span, page: int) -> None:
        entry = self.inventory.setdefault(span.entity_type, {"count": 0, "pages": []})
        entry["count"] += 1
        if page not in entry["pages"]:
            entry["pages"].append(page)

    # -- public ------------------------------------------------------------

    def process_page(self, text: str, page: int) -> TokenizedPage:
        spans = detect_spans(text)
        replacements: List[Tuple[Span, str]] = []

        for span in spans:
            self._record_inventory(span, page)
            if span.entity_type in self._tokenize_types:
                replacements.append((span, self._token_for(span, page)))
            elif span.entity_type in self._redact_types:
                replacements.append((span, f"<{span.entity_type}>"))

        if not self.rewrite_text:
            return TokenizedPage(page=page, text=text, spans=spans)

        # Replace from the end so earlier offsets stay valid.
        out = text
        for span, replacement in sorted(replacements, key=lambda r: r[0].start, reverse=True):
            out = out[: span.start] + replacement + out[span.end :]
        return TokenizedPage(page=page, text=out, spans=spans)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render(text: str, vault: Dict[str, Dict[str, str]]) -> Tuple[str, int]:
    """Replace tokens in ``text`` with their vault values.

    Returns the rendered text and the number of substitutions made. Redaction
    tags (``<US_SSN>``) have no vault entry and are left untouched.
    """
    if not text or not vault:
        return text, 0
    count = 0

    def _sub(match: "re.Match[str]") -> str:
        nonlocal count
        entry = vault.get(match.group(0))
        if entry is None:
            return match.group(0)
        count += 1
        return entry["value"]

    return TOKEN_RE.sub(_sub, text), count


def render_value(token: Optional[str], vault: Dict[str, Dict[str, str]]) -> Optional[str]:
    if token is None:
        return None
    entry = vault.get(token)
    return entry["value"] if entry else None
