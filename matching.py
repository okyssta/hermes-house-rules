"""Safe regex matching over tool-call arguments.

Three properties matter here, and each is enforced rather than promised:

* **Regex only, never code.** A pattern is handed to :func:`re.compile` and
  nothing else. There is no ``eval``, no import-by-string, no shell — a pattern
  read out of a repository's ``.hermes/rules.json`` can only ever be a regex.
* **Bounded work.** Patterns are capped at :data:`MAX_PATTERN_CHARS`, candidate
  text at :data:`MAX_TEXT_CHARS`, and compiled patterns live in a small LRU so a
  rule is compiled once per process rather than once per tool call.
* **No surprise matching.** A rule names the field it is about (``command``,
  ``path``, ``url`` or ``all``); the text for that field is built from the tool
  arguments in a fixed, documented order, so "why did this fire?" always has an
  answer.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable

# A pattern longer than this is a configuration mistake or an attack, not a rule.
MAX_PATTERN_CHARS = 500
# Terminal commands are long; 8k characters is far past any real command, and it
# keeps a pathological regex from turning one tool call into a multi-second stall.
MAX_TEXT_CHARS = 8000

_CACHE_LIMIT = 256
_CACHE: "OrderedDict[str, re.Pattern[str] | None]" = OrderedDict()

# Ordered by how specific they are: a tool that carries both `path` and
# `file_path` should be matched on the same text either way.
_COMMAND_KEYS = ("command", "cmd", "shell_command", "script")
_PATH_KEYS = (
    "path", "file_path", "filepath", "filename", "directory", "dir",
    "target", "destination", "source", "cwd", "workdir",
)
_URL_KEYS = ("url", "uri", "endpoint", "link", "href")

MATCH_MODES = ("command", "path", "url", "all")


def compile_pattern(pattern: Any) -> "re.Pattern[str] | None":
    """Compile a rule pattern, or return ``None`` when it cannot be used.

    ``None`` covers every reason a pattern is unusable — wrong type, empty, too
    long, invalid syntax, or a repetition count Python refuses to compile — so a
    single bad rule can be skipped without the caller guessing why.
    """
    if not isinstance(pattern, str):
        return None
    text = pattern.strip()
    if not text or len(text) > MAX_PATTERN_CHARS:
        return None
    cached = _CACHE.get(text, ...)
    if cached is not ...:
        _CACHE.move_to_end(text)
        return cached
    try:
        compiled: "re.Pattern[str] | None" = re.compile(text)
    except (re.error, RecursionError, OverflowError, ValueError):
        compiled = None
    _CACHE[text] = compiled
    while len(_CACHE) > _CACHE_LIMIT:
        _CACHE.popitem(last=False)
    return compiled


def clear_cache() -> None:
    """Drop the compiled-pattern cache (tests, and after a rule is edited)."""
    _CACHE.clear()


@dataclass(frozen=True)
class Hit:
    """One match: which field it was found in, the text, and the surrounding call."""

    field: str
    matched: str
    context: str


def _cap(text: str) -> str:
    return text if len(text) <= MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS]


def _string_values(args: dict[str, Any], keys: Iterable[str]) -> list[str]:
    found: list[str] = []
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            found.append(_cap(value))
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item.strip():
                    found.append(_cap(item))
    return found


def candidate_texts(args: Any) -> dict[str, tuple[str, ...]]:
    """The text each ``match_on`` mode is evaluated against, for one tool call.

    ``command`` and ``path`` and ``url`` are narrow on purpose: a rule about
    shell commands must not fire because a file *named* ``pip install`` was read.
    ``all`` is the wide net — the plain command/path/url text first, then the
    whole call as sorted JSON, so a rule can match either a readable command or a
    nested argument without the user writing two rules.
    """
    if not isinstance(args, dict):
        args = {}
    command = _string_values(args, _COMMAND_KEYS)
    path = _string_values(args, _PATH_KEYS)
    url = _string_values(args, _URL_KEYS)
    wide: list[str] = [*command, *path, *url]
    try:
        dumped = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - default=str makes this rare
        dumped = ""
    if dumped and dumped not in wide:
        wide.append(_cap(dumped))
    return {
        "command": tuple(command),
        "path": tuple(path),
        "url": tuple(url),
        "all": tuple(wide),
    }


def texts_for(match_on: str, args: Any) -> tuple[str, ...]:
    """Candidate texts for *match_on*, falling back to ``all`` for unknown modes."""
    candidates = candidate_texts(args)
    return candidates.get(match_on if match_on in MATCH_MODES else "all", candidates["all"])


def _context(text: str, start: int, end: int, width: int = 60) -> str:
    left = max(0, start - width)
    right = min(len(text), end + width)
    snippet = " ".join(text[left:right].split())
    if left > 0:
        snippet = "…" + snippet
    if right < len(text):
        snippet = snippet + "…"
    return snippet[:240]


def search(pattern: str, texts: Iterable[str], field: str = "command") -> Hit | None:
    """First match of *pattern* across *texts*, with a quotable snippet.

    *field* is only a label (which argument group the texts came from) so the
    caller can tell the user where the rule fired.
    """
    regex = compile_pattern(pattern)
    if regex is None:
        return None
    for text in texts:
        if not text:
            continue
        text = _cap(text)  # a caller that skipped candidate_texts is still bounded
        try:
            match = regex.search(text)
        except (RecursionError, MemoryError):  # pragma: no cover - defensive
            return None
        if match is None:
            continue
        raw = match.group(0)
        if not isinstance(raw, str):  # pragma: no cover - groups are strings here
            raw = str(raw)
        if not raw:
            # Zero-width match (`^`, `\b`, lookahead): there is no text to quote,
            # so quote what is around it — otherwise the block message would say
            # "matched: ''" and teach the user nothing.
            raw = _context(text, match.start(), match.end(), width=40)
        matched = " ".join(raw.split())
        if len(matched) > 160:
            matched = matched[:157] + "…"
        return Hit(field=field, matched=matched, context=_context(text, match.start(), match.end()))
    return None
