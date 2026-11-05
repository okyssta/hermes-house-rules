"""Tool handlers: ``rule_add``, ``rule_list``, ``rule_remove``, ``rule_test``.

Contract, applied literally: ``def handler(args: dict, **kwargs) -> str``, never
raises, always returns text a model can act on. Every handler answers with the
facts (rule ids, file paths, what would fire and why) because the model's next
move — telling the user, testing another input, removing a rule — depends on them.
"""

from __future__ import annotations

from typing import Any

from . import rules
from .settings import Settings


def get_settings() -> Settings:
    from . import current_settings

    return current_settings()


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


# ─────────────────────────────────────────────────────────────── rule_add


def rule_add(args: dict | None = None, **kwargs: Any) -> str:
    data = args or {}
    settings = get_settings()
    if not settings.enabled:
        return "house_rules is disabled (enabled=false), so no rule would be enforced. Enable it first."

    scope = _text(data.get("scope") or "project").strip().lower()
    project = rules.project_root() if scope == "project" else None
    rule, note = rules.add_rule(
        settings,
        pattern=_text(data.get("pattern")),
        tool=_text(data.get("tool") or "terminal"),
        action=_text(data.get("action") or "block"),
        scope=scope,
        message=_text(data.get("message")),
        name=_text(data.get("name")) or None,
        match_on=_text(data.get("match_on") or "command"),
        project=project,
    )
    if rule is None:
        return f"Rule not added: {note}"
    if note:
        return (
            f"{note}\n"
            f"  {rule.id} · {rule.tool} · {rule.match_on} · {rule.action} · {rule.where}\n"
            f"  pattern: {rule.pattern}"
        )

    lines = [
        f"Added rule {rule.id} — {rule.name}",
        f"  pattern: {rule.pattern}",
        f"  matches: {rule.tool} calls, on {rule.match_on} text, with Python regex search()",
        f"  action: {rule.action} · scope: {rule.where}",
        f"  why: {rule.explains}",
        f"  stored in: {rule.file}",
        "",
        "It is enforced before every matching tool call from now on, in this session and future ones.",
        f"Check it with rule_test(tool={rule.tool!r}, args_text=...) or /rules test {rule.tool} <text>.",
        f"Remove it with /rules remove {rule.id}.",
    ]
    if scope == "project":
        lines.append(
            "Tip: commit .hermes/rules.json if the whole team should follow this rule; "
            "the plugin reads it from the project root, not from your profile."
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────── rule_list


def _rule_line(rule: rules.Rule) -> str:
    hits = rules.hit_count(rule.id)
    action = rule.action or "(default)"
    flag = "  " if rule.action else "· "
    return (
        f"{flag}{rule.id}  [{action:<7}] {rule.tool:<10} {rule.match_on:<7} "
        f"hits {hits:<4} {rule.pattern}"
    )


def render_rule_list(scope: str = "all", settings: Settings | None = None, project: Any = None) -> str:
    settings = settings or get_settings()
    scope = (scope or "all").strip().lower()
    project_dir = rules.project_root(project)
    all_rules, problems = rules.load_rules(settings, project_dir)
    shown = [rule for rule in all_rules if scope == "all" or rule.scope == scope]

    header = (
        f"house_rules · {len(all_rules)} rule(s) "
        f"({sum(1 for r in all_rules if r.scope == 'project')} project, "
        f"{sum(1 for r in all_rules if r.scope == 'global')} global)"
    )
    lines = [header, f"project root: {project_dir}", ""]
    if not shown:
        lines.append("No rules here yet. Add one with /rules add <regex> [tool] or rule_add.")
    for group in ("project", "global"):
        group_rules = [rule for rule in shown if rule.scope == group]
        if not group_rules:
            continue
        lines.append("project rules:" if group == "project" else "global rules:")
        for rule in group_rules:
            lines.append(_rule_line(rule))
            lines.append(f"    {rule.explains}"[:200])
        lines.append("")
    for problem in problems:
        lines.append(f"! {problem}")
    if not settings.enabled:
        lines.append("! enabled=false — rules are listed but nothing is enforced right now.")
    if shown and settings.log_hits:
        last = rules.recent_hits(5)
        if last:
            lines.append("recent hits:")
            for item in last:
                lines.append(
                    f"  {str(item.get('rule_id'))} · {str(item.get('tool'))} · "
                    f"{str(item.get('action'))} · \"{str(item.get('matched'))[:80]}\""
                )
    return "\n".join(lines).rstrip()


def rule_list(args: dict | None = None, **kwargs: Any) -> str:
    data = args or {}
    return render_rule_list(_text(data.get("scope") or "all"))


# ─────────────────────────────────────────────────────────────── rule_remove


def rule_remove(args: dict | None = None, **kwargs: Any) -> str:
    data = args or {}
    settings = get_settings()
    rule_id = _text(data.get("rule_id")).strip()
    removed, note = rules.remove_rule(settings, rule_id)
    if removed is None:
        return f"Rule not removed: {note}"
    return (
        f"Removed rule {removed.id} — {removed.name}\n"
        f"  pattern: {removed.pattern} · scope: {removed.where}\n"
        f"  file: {removed.file}\n"
        "It no longer applies to any tool call. The previous file contents are beside it as rules.json.bak."
    )


# ─────────────────────────────────────────────────────────────── rule_test


_STATUS = {
    "match": "MATCH    ",
    "no-match": "no match ",
    "tool": "skipped  ",
    "bad-pattern": "broken   ",
}


def _reason(row: rules.Inspection, tool: str) -> str:
    if row.status == "tool":
        return f"rule is for tool {row.rule.tool!r}, not {tool!r}"
    if row.status == "bad-pattern":
        return "pattern does not compile — this rule cannot fire (fix or remove it)"
    if row.status == "no-match":
        return f"pattern {row.rule.pattern!r} did not match the {row.rule.match_on} text"
    return ""


def render_rule_test(tool: str, args_text: str, settings: Settings) -> str:
    tool = (tool or "terminal").strip()
    text = args_text if isinstance(args_text, str) else str(args_text)
    # The tester treats the text as whatever field the rule cares about: the user
    # is pasting the thing they want to check ("pip install requests", a path, a URL).
    probe = {"command": text, "path": text, "url": text}
    rows, problems = rules.scan_rules(tool, probe, settings)
    decision = rules.evaluate(tool, probe, settings, record=False)

    lines = [
        f"rule_test · tool={tool} · text={text!r}",
        f"checked {len(rows)} rule(s): project first, then global, newest first",
        "",
    ]
    if not rows:
        lines.append("No rules are configured, so nothing would fire.")
    for row in rows:
        rule = row.rule
        flag = "✓" if row.status == "match" else " "
        lines.append(
            f"{flag} {_STATUS.get(row.status, row.status)} {rule.id} "
            f"[{row.action or rule.action or '(default)'}] {rule.scope} · tool {rule.tool} · "
            f"{rule.match_on} · {rule.pattern}"
        )
        if row.status == "match" and row.hit is not None:
            lines.append(f"    matched in {row.hit.field}: \"{row.hit.matched}\"")
            lines.append(f"    why: {rule.explains}")
        else:
            lines.append(f"    {_reason(row, tool)}")
    for problem in problems:
        lines.append(f"! {problem}")

    lines.append("")
    if decision is None:
        lines.append("decision: PASS — no rule matches this call; it would run.")
    elif decision.action == "block":
        lines.append(f"decision: BLOCK — rule {decision.rule.id} would stop this call.")
    elif decision.action == "approve":
        lines.append(f"decision: APPROVE — rule {decision.rule.id} would ask a human first.")
    else:
        lines.append(
            f"decision: WARN — rule {decision.rule.id} would record a hit and let the call run"
            + (" (warn_blocks is on, so it would block)" if decision.escalated else "")
        )
    if settings.warn_blocks and decision is not None and decision.declared_action == "warn":
        lines.append("note: warn_blocks is on, so warn rules stop calls instead of logging them.")
    lines.append("nothing was executed and no hit was recorded.")
    return "\n".join(lines)


def rule_test(args: dict | None = None, **kwargs: Any) -> str:
    data = args or {}
    tool = _text(data.get("tool") or "terminal").strip()
    args_text = _text(data.get("args_text"))
    if not args_text.strip():
        return (
            "rule_test needs args_text — the command, path or URL to check, e.g. "
            "rule_test(tool='terminal', args_text='pip install requests')."
        )
    return render_rule_test(tool, args_text, get_settings())
