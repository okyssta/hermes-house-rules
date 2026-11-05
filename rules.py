"""Rule storage, evaluation and hit accounting.

Storage layout (all paths under the active Hermes home unless noted):

    <hermes_home>/plugin-data/house_rules/rules.json    global rules
    <hermes_home>/plugin-data/house_rules/hits.json     counters + last 200 hits
    <project>/.hermes/rules.json                        project rules (opt-in read)

The two rule files share one schema::

    {"version": 1, "rules": [
      {"id": "hr_ab12cd34", "name": "...", "pattern": "pip\\s+install",
       "tool": "terminal", "action": "block", "match_on": "command",
       "message": "why this rule exists", "created_at": 1762345678.0}
    ]}

A hand-written entry may omit ``id``, ``name``, ``action``, ``tool`` and
``match_on``; those are derived. ``id`` is a hash of the rule's identity (scope,
project, tool, match mode, pattern), so it is stable across reloads without
having to be written down.

Reads are deliberately forgiving in one direction only: a *bad entry* is skipped
and reported, but it never invalidates its neighbours, and an unreadable project
file never disables the global rules. A rule that does match is therefore never
silently skipped because something else in the file was broken.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import matching
from .settings import ACTIONS, PLUGIN_ID, Settings

logger = logging.getLogger(__name__)

# Tools this plugin registers. They are never subject to rules: a rule that
# blocked `rule_remove` would be impossible to disarm from inside the agent.
PLUGIN_TOOLS = frozenset({"rule_add", "rule_list", "rule_remove", "rule_test"})

MAX_RULES_HARD = 1000
RECENT_HITS_KEPT = 200
RULE_ID_RE = re.compile(r"^hr_[0-9a-f]{8}$")

_STORE_LOCK = threading.Lock()


# ─────────────────────────────────────────────────────────────── locations


def hermes_home() -> Path:
    """The active Hermes home.

    ``HERMES_HOME`` wins when set (that is what the host resolves too), then
    ``hermes_constants.get_hermes_home()``. The fallback chain exists so the
    plugin still works in a bare interpreter — a test harness or the demo script —
    without importing the host.
    """
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:  # pragma: no cover - bare interpreter
        return Path.home() / ".hermes"


def data_dir() -> Path:
    return hermes_home() / "plugin-data" / PLUGIN_ID


def global_path() -> Path:
    return data_dir() / "rules.json"


def hits_path() -> Path:
    return data_dir() / "hits.json"


def project_root(start: str | Path | None = None) -> Path:
    """Nearest git root from *start* (default: cwd), else *start* itself."""
    try:
        base = Path(start or os.getcwd()).expanduser()
    except OSError:  # pragma: no cover - cwd deleted under us
        base = Path.home()
    try:
        base = base.resolve()
    except OSError:  # pragma: no cover
        pass
    for candidate in (base, *base.parents):
        try:
            if (candidate / ".git").exists():
                return candidate
        except OSError:  # pragma: no cover - unreadable parent
            continue
    return base


def project_rules_path(project: str | Path | None = None) -> Path:
    """Where a project's rules live: ``<root>/.hermes/rules.json``."""
    root = Path(project) if project is not None else project_root()
    return root / ".hermes" / "rules.json"


# ─────────────────────────────────────────────────────────────── the rule


def _clean(text: Any, limit: int = 500) -> str:
    return " ".join(str(text or "").split())[:limit]


def make_rule_id(scope: str, project: str, tool: str, match_on: str, pattern: str) -> str:
    seed = "\x1f".join((scope, project if scope == "project" else "", tool, match_on, pattern))
    return "hr_" + hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:8]


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    pattern: str
    tool: str = "terminal"
    action: str = "block"
    match_on: str = "command"
    message: str = ""
    created_at: float = 0.0
    scope: str = "project"
    project: str = ""

    @property
    def where(self) -> str:
        return f"project ({self.project})" if self.scope == "project" else "global"

    @property
    def file(self) -> Path:
        return Path(self.project) / ".hermes" / "rules.json" if self.scope == "project" else global_path()

    @property
    def explains(self) -> str:
        return self.message or "no explanation was recorded for this rule"

    def applies_to(self, tool_name: str) -> bool:
        return self.tool in {"*", "any", "all"} or self.tool == tool_name

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "pattern": self.pattern,
            "tool": self.tool,
            "action": self.action,
            "match_on": self.match_on,
            "message": self.message,
            "created_at": self.created_at,
        }
        return data

    @classmethod
    def from_dict(cls, entry: Any, *, scope: str, project: str = "") -> "Rule | None":
        """One entry from a rules file; ``None`` when it cannot be a rule.

        Scope always comes from *which file* the entry was read from, never from
        the entry itself: a project file cannot promote its rules to global.
        """
        if not isinstance(entry, dict):
            return None
        pattern = entry.get("pattern")
        if pattern is None:
            pattern = entry.get("regex")  # tolerating a hand-written synonym
        if not isinstance(pattern, str) or not pattern.strip():
            return None
        pattern = pattern.strip()
        if len(pattern) > matching.MAX_PATTERN_CHARS:
            return None
        tool = _clean(entry.get("tool") or "*", 80) or "*"
        match_on = str(entry.get("match_on") or "command").strip().lower()
        if match_on not in matching.MATCH_MODES:
            match_on = "all"
        action = str(entry.get("action") or "").strip().lower()
        if action not in ACTIONS:
            action = ""
        message = _clean(entry.get("message"), 600)
        name = _clean(entry.get("name"), 80) or _clean(message, 60) or _clean(pattern, 60)
        try:
            created_at = float(entry.get("created_at") or 0.0)
        except (TypeError, ValueError):
            created_at = 0.0
        if created_at <= 0:
            created_at = 0.0
        rule_id = str(entry.get("id") or "").strip()
        if not RULE_ID_RE.match(rule_id):
            rule_id = make_rule_id(scope, project, tool, match_on, pattern)
        return cls(
            id=rule_id, name=name, pattern=pattern, tool=tool, action=action,
            match_on=match_on, message=message, created_at=created_at,
            scope=scope, project=project if scope == "project" else "",
        )


@dataclass(frozen=True)
class Decision:
    """What the policy layer decided about one tool call."""

    rule: Rule
    action: str  # effective: serves action, after warn_blocks escalation
    hit: matching.Hit
    declared_action: str = "block"
    escalated: bool = False

    @property
    def rule_key(self) -> str:
        return f"house_rules:{self.rule.id}"


# ─────────────────────────────────────────────────────────────── file io


def _atomic_write(path: Path, payload: dict[str, Any], *, backup: bool = True) -> None:
    """Write JSON beside the target, then rename it into place.

    ``os.replace`` is atomic within a filesystem, so a crash leaves either the old
    file or the new one — never a half-written rules file. When *backup* is set
    and the file already exists, the previous contents are copied to
    ``<name>.bak`` first: the user's own policy file is not something to lose.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.is_file():
        try:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        except OSError as exc:  # pragma: no cover - read-only dir
            logger.debug("house_rules: could not back up %s: %s", path, exc)
    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(3)}.tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:  # pragma: no cover - some filesystems refuse fsync
            pass
    os.replace(temp, path)


def _read_rules_file(path: Path, *, scope: str, project: str = "") -> tuple[list[Rule], list[str]]:
    """Rules in one file plus human-readable problems; never raises."""
    if not path.is_file():
        return [], []
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as exc:
        return [], [f"{path}: unreadable ({type(exc).__name__}) — ignored"]
    entries = raw.get("rules") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return [], [f"{path}: no 'rules' list — ignored"]
    rules: list[Rule] = []
    problems: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        rule = Rule.from_dict(entry, scope=scope, project=project)
        if rule is None:
            problems.append(f"{path}: entry #{index} skipped (not a usable rule)")
            continue
        if rule.id in seen:
            problems.append(f"{path}: entry #{index} duplicates {rule.id} — skipped")
            continue
        seen.add(rule.id)
        rules.append(rule)
    return rules, problems


def load_rules(settings: Settings, project: str | Path | None = None) -> tuple[list[Rule], list[str]]:
    """Every rule that applies here, in evaluation order, plus load problems.

    Order is the policy order: project rules first, then global; newest first
    within each group. Nothing is sorted by the order the files happened to list.
    """
    project_dir = Path(project) if project is not None else project_root()
    rules: list[Rule] = []
    problems: list[str] = []

    if settings.project_file_enabled:
        project_rules, project_problems = _read_rules_file(
            project_dir / ".hermes" / "rules.json", scope="project", project=str(project_dir)
        )
        rules.extend(project_rules)
        problems.extend(project_problems)
    global_rules, global_problems = _read_rules_file(global_path(), scope="global")
    rules.extend(global_rules)
    problems.extend(global_problems)

    rules.sort(key=lambda rule: (0 if rule.scope == "project" else 1, -rule.created_at))
    # Evaluation is capped by the hard ceiling, not by ``max_rules``: that setting
    # governs how many rules the plugin will *add*, and a hand-written file that
    # exceeds it must not have its extra rules silently dropped from enforcement.
    return rules[:MAX_RULES_HARD], problems


# ─────────────────────────────────────────────────────────────── hits


def _read_hits() -> dict[str, Any]:
    try:
        raw = json.loads(hits_path().read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {"counters": {}, "recent": []}
    if not isinstance(raw, dict):
        return {"counters": {}, "recent": []}
    counters = raw.get("counters")
    recent = raw.get("recent")
    return {
        "counters": counters if isinstance(counters, dict) else {},
        "recent": recent if isinstance(recent, list) else [],
    }


def record_hit(decision: Decision, tool_name: str, settings: Settings) -> None:
    """Count one firing; keep the last :data:`RECENT_HITS_KEPT` when enabled.

    Only the matched substring is stored, never the whole call: the hit log is a
    policy audit trail, not a command history. Best effort — a hit log that
    cannot be written must not turn into a failed tool call.
    """
    try:
        with _STORE_LOCK:
            state = _read_hits()
            counters = state["counters"]
            try:
                counters[decision.rule.id] = int(counters.get(decision.rule.id) or 0) + 1
            except (TypeError, ValueError):
                counters[decision.rule.id] = 1
            if settings.log_hits:
                recent = state["recent"]
                recent.insert(
                    0,
                    {
                        "at": round(time.time(), 3),
                        "rule_id": decision.rule.id,
                        "name": decision.rule.name,
                        "tool": _clean(tool_name, 80),
                        "action": decision.action,
                        "matched": decision.hit.matched[:160],
                        "scope": decision.rule.scope,
                    },
                )
                del recent[RECENT_HITS_KEPT:]
            _atomic_write(hits_path(), state, backup=False)
    except Exception:  # pragma: no cover - hit accounting is never load-bearing
        logger.debug("house_rules: could not record hit", exc_info=True)


def hit_count(rule_id: str) -> int:
    try:
        return int(_read_hits()["counters"].get(rule_id) or 0)
    except (TypeError, ValueError):
        return 0


def recent_hits(limit: int = 10) -> list[dict[str, Any]]:
    items = _read_hits()["recent"]
    return [item for item in items[:limit] if isinstance(item, dict)]


# ─────────────────────────────────────────────────────────────── evaluate


def _effective_action(rule: Rule, settings: Settings) -> tuple[str, bool]:
    action = rule.action if rule.action in ACTIONS else settings.default_action
    if action == "warn" and settings.warn_blocks:
        return "block", True
    return action, False


@dataclass(frozen=True)
class Inspection:
    """One rule's verdict for one call, without running anything."""

    rule: Rule
    status: str  # "match" | "no-match" | "tool" | "bad-pattern"
    hit: matching.Hit | None = None
    action: str = ""
    escalated: bool = False


def scan_rules(
    tool_name: str, args: Any, settings: Settings, project: str | Path | None = None
) -> tuple[list[Inspection], list[str]]:
    """Every rule in policy order with its verdict for this call (no side effects)."""
    name = str(tool_name or "").strip()
    rules, problems = load_rules(settings, project)
    rows: list[Inspection] = []
    for rule in rules:
        if not name or not rule.applies_to(name):
            rows.append(Inspection(rule=rule, status="tool"))
            continue
        if matching.compile_pattern(rule.pattern) is None:
            rows.append(Inspection(rule=rule, status="bad-pattern"))
            continue
        hit = matching.search(
            rule.pattern, matching.texts_for(rule.match_on, args), field=rule.match_on
        )
        action, escalated = _effective_action(rule, settings)
        rows.append(
            Inspection(
                rule=rule,
                status="match" if hit is not None else "no-match",
                hit=hit,
                action=action,
                escalated=escalated,
            )
        )
    return rows, problems


def evaluate(
    tool_name: str,
    args: Any,
    settings: Settings,
    project: str | Path | None = None,
    *,
    record: bool = True,
) -> Decision | None:
    """Decide one tool call.

    Every matching rule is collected, then the strongest *effective* action wins:
    block beats approve beats warn, and within one action the first rule in
    policy order (project before global, newest first) is the one reported. That
    ordering matters — it means a project rule saying "block" is not softened by
    an older global rule saying "warn".

    A decision is only returned for a rule that matched; the caller runs the tool
    when this returns ``None``.
    """
    name = str(tool_name or "").strip()
    if not name or name in PLUGIN_TOOLS:
        return None

    rows, problems = scan_rules(name, args, settings, project)
    for problem in problems:
        logger.warning("house_rules: %s", problem)

    matched = [row for row in rows if row.status == "match" and row.hit is not None]
    if not matched:
        return None
    # Strongest action wins, and within one action the first match in policy
    # order is reported; a veto is never softened by a later warn.
    chosen = next(
        (row for wanted in ("block", "approve", "warn") for row in matched if row.action == wanted),
        None,
    )
    if chosen is None:  # pragma: no cover - actions are constrained to the three
        return None
    decision = Decision(
        rule=chosen.rule,
        action=chosen.action,
        hit=chosen.hit,
        declared_action=chosen.rule.action or settings.default_action,
        escalated=chosen.escalated,
    )
    if record:
        record_hit(decision, name, settings)
    return decision


def decision_message(decision: Decision) -> str:
    """The text the model (and the user) sees for a block or an approval."""
    rule = decision.rule
    if decision.action == "block":
        header = f"house_rules blocked this tool call — rule {rule.id} \"{rule.name}\""
        tail = f"To change it: /rules remove {rule.id} — or edit {rule.file}"
        if decision.escalated:
            tail = (
                "This is a warn rule and warn_blocks is on, so it stopped the call. "
                f"To change it: /rules remove {rule.id} — or edit {rule.file}"
            )
    else:
        header = f"house_rules wants your approval before this tool call — rule {rule.id} \"{rule.name}\""
        tail = f"Approve only if this is intended. To stop asking: /rules remove {rule.id} — or edit {rule.file}"

    lines = [
        header,
        f"  matched in {decision.hit.field}: \"{decision.hit.matched}\"",
        f"  why: {rule.explains}",
        f"  rule: pattern `{rule.pattern}` · tool {rule.tool} · scope {rule.where} · action {decision.action}",
    ]
    if decision.hit.context and decision.hit.context.strip() != decision.hit.matched.strip():
        lines.append(f"  in context: {decision.hit.context}")
    lines.append(f"  {tail}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────── mutations


def _write_rules(path: Path, rules: Iterable[Rule], *, scope: str, project: str = "") -> None:
    entries = [rule.to_dict() for rule in rules]
    _atomic_write(path, {"version": 1, "rules": entries})


def validate_pattern(pattern: Any) -> str:
    """Return a normalised pattern or raise ``ValueError`` with a usable reason."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("a rule needs a pattern (a Python regular expression)")
    text = pattern.strip()
    if len(text) > matching.MAX_PATTERN_CHARS:
        raise ValueError(f"pattern is longer than {matching.MAX_PATTERN_CHARS} characters")
    if matching.compile_pattern(text) is None:
        raise ValueError(f"pattern is not a valid regular expression: {text!r}")
    return text


def add_rule(
    settings: Settings,
    *,
    pattern: str,
    tool: str = "terminal",
    action: str = "block",
    scope: str = "project",
    message: str = "",
    name: str | None = None,
    match_on: str = "command",
    project: str | Path | None = None,
) -> tuple[Rule | None, str]:
    """Create a rule; returns ``(rule, note)`` — exactly one is meaningful.

    ``note`` carries either the refusal reason or a short "already exists"
    message, so the tool can be honest without raising.
    """
    scope = (scope or "project").strip().lower()
    if scope not in {"project", "global"}:
        return None, "scope must be 'project' or 'global'"
    if scope == "project" and not settings.project_file_enabled:
        return None, (
            "project rule files are disabled (project_file_enabled=false); "
            "add this rule with scope='global' or enable project files"
        )
    tool = _clean(tool or "terminal", 80) or "terminal"
    match_on = (match_on or "command").strip().lower()
    if match_on not in matching.MATCH_MODES:
        return None, f"match_on must be one of {', '.join(matching.MATCH_MODES)}"
    action = (action or "block").strip().lower()
    if action not in ACTIONS:
        return None, f"action must be one of {', '.join(ACTIONS)}"
    try:
        pattern = validate_pattern(pattern)
    except ValueError as exc:
        return None, str(exc)

    project_dir = Path(project) if project is not None else project_root()
    project_key = str(project_dir) if scope == "project" else ""
    rule = Rule(
        id=make_rule_id(scope, project_key, tool, match_on, pattern),
        name=_clean(name, 80) or _clean(message, 60) or _clean(pattern, 60),
        pattern=pattern,
        tool=tool,
        action=action,
        match_on=match_on,
        message=_clean(message, 600),
        created_at=round(time.time(), 3),
        scope=scope,
        project=project_key,
    )

    path = rule.file
    with _STORE_LOCK:
        existing, _ = _read_rules_file(path, scope=scope, project=project_key)
        for other in existing:
            if other.id == rule.id:
                return other, f"rule {other.id} already exists — nothing changed"
        if len(existing) >= max(1, min(settings.max_rules, MAX_RULES_HARD)):
            return None, f"{len(existing)} rules already in {path}; max_rules is {settings.max_rules}"
        _write_rules(path, [*existing, rule], scope=scope, project=project_key)
    matching.clear_cache()
    return rule, ""


def remove_rule(
    settings: Settings, rule_id: str, project: str | Path | None = None
) -> tuple[Rule | None, str]:
    """Delete a rule from the file that owns it."""
    rule_id = str(rule_id or "").strip()
    if not RULE_ID_RE.match(rule_id):
        return None, "a rule id looks like hr_1a2b3c4d — see /rules list"
    project_dir = Path(project) if project is not None else project_root()
    candidates: list[tuple[Path, str, str]] = [
        (project_dir / ".hermes" / "rules.json", "project", str(project_dir)),
        (global_path(), "global", ""),
    ]
    for path, scope, project_key in candidates:
        with _STORE_LOCK:
            rules, _ = _read_rules_file(path, scope=scope, project=project_key)
            keep = [rule for rule in rules if rule.id != rule_id]
            if len(keep) == len(rules):
                continue
            removed = next(rule for rule in rules if rule.id == rule_id)
            if keep:
                _write_rules(path, keep, scope=scope, project=project_key)
            else:
                # Keep a valid, empty store rather than deleting the file: the
                # user may have other tooling pointed at the path.
                _write_rules(path, [], scope=scope, project=project_key)
            matching.clear_cache()
            return removed, ""
    return None, f"no rule with id {rule_id} in {project_dir}/.hermes/rules.json or {global_path()}"
