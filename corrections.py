"""Vocabulary correction for RTT transcripts.

Post-transcription find/replace to fix recurring domain-term garbles the STT
model produces (e.g. "high trust" -> "HITRUST", "a car" -> "ACR"). Rules live in
`corrections.json`. Two rule kinds:

- literal:  unconditional whole-word/phrase replacement. Use ONLY when the wrong
            form never occurs legitimately in the user's meetings.
- guarded:  replace only when a context word (`near`) appears in the same line.
            Use when the wrong form is also a real word ("fire" -> "FHIR" only
            near epic/hl7/interop/...), so "rapid fire" is left alone.

Applied per emitted line at the chunked-backend emit choke point. Whole-word,
case-insensitive matching; the replacement string's casing is used verbatim.
Fails open: any load/parse error disables corrections rather than killing a run.
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional


class Corrections:
    """Loads correction rules and applies them to transcript lines."""

    def __init__(self, literal: Optional[list] = None, guarded: Optional[list] = None) -> None:
        self._literal = []  # list of (compiled_pattern, replacement)
        self._guarded = []  # list of (compiled_pattern, replacement, [near_words_lower])
        for rule in literal or []:
            wrong = (rule.get("wrong") or "").strip()
            right = rule.get("right")
            if not wrong or right is None:
                continue
            self._literal.append((self._compile(wrong), right))
        for rule in guarded or []:
            wrong = (rule.get("wrong") or "").strip()
            right = rule.get("right")
            near = [w.lower() for w in (rule.get("near") or []) if w]
            avoid = [w.lower() for w in (rule.get("avoid") or []) if w]
            if not wrong or right is None or not near:
                continue
            self._guarded.append((self._compile(wrong), right, near, avoid))

    @staticmethod
    def _compile(phrase: str) -> "re.Pattern":
        # Whole-word (or whole-phrase) boundary, case-insensitive.
        return re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)

    @property
    def active(self) -> bool:
        return bool(self._literal or self._guarded)

    def apply(self, line: str) -> str:
        """Return `line` with all applicable corrections applied.

        Guarded rules gate on the ORIGINAL line's lowercase content so one
        rule's replacement can't accidentally satisfy another's context test.
        """
        if not line:
            return line
        gate = line.lower()
        out = line
        for pat, repl in self._literal:
            out = pat.sub(repl, out)
        for pat, repl, near, avoid in self._guarded:
            if any(w in gate for w in avoid):
                continue  # protected idiom/phrase present → leave the word alone
            if any(w in gate for w in near):
                out = pat.sub(repl, out)
        return out

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Corrections":
        """Load rules from `path` (default: corrections.json beside this file).

        Fails open: missing file or bad JSON yields an inert (no-op) instance.
        Override path via env RTT_CORRECTIONS_FILE; set it to empty/"none" to
        disable corrections entirely.
        """
        env = os.environ.get("RTT_CORRECTIONS_FILE")
        if env is not None:
            if env.strip().lower() in ("", "none", "off", "0", "false"):
                return cls()
            path = env
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corrections.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls(literal=data.get("literal"), guarded=data.get("guarded"))
