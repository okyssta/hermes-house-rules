"""/rules — list, add, remove and test the standing rules.

A command is the surface a person reaches for, so it answers in sentences and
keeps its output to a screen. It runs in the gateway process, so it returns text
instead of printing, and the only state it changes is this plugin's own rule
files.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from . import rules, tools

# A second positional token only counts as a tool name when it looks like one
# ("terminal", "write_file", "mcp__x__y"); anything else is part of the pattern.
_TOOLISH = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")

_HELP = (
    "/rules                          status: how many rules, recent hits\n"
    "/rules list [project|global]    every rule, in check order\n"
    "/rules add <regex> [tool] [flags]  add a rule (project scope by default)\n"
    "     --global            store in your profile, apply everywhere\n"
    "     --warn / --approve / --block   action (default block)\n"
    "     --path / --url / --all        match path/URL text or the whole call\n"
    "     --why \"sentence\"    explain the rule to your future self\n"
    "/rules remove <id>              delete a rule\n"
    "/rules test <tool> <text>       would this call be blocked, and by which rule?\n"
    "/rules hits                     the last recorded firings"
)


def _settings():
    from . import current_settings

    return current_settings()


def _split(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        # Unbalanced quotes: fall back to whitespace rather than refusing the
        # whole command — the user can still add their rule.
        return text.split()


def _parse_flags(tokens: list[str]) -> tuple[list[str], dict[str, Any], str]:
    positionals: list[str] = []
    flags: dict[str, Any] = {"scope": "project", "action": "block", "match_on": "command", "message": ""}
    error = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--global" or token == "-g":
            flags["scope"] = "global"
        elif token in {"--project", "-p"}:
            flags["scope"] = "project"
        elif token == "--warn":
            flags["action"] = "warn"
        elif token == "--approve":
            flags["action"] = "approve"
        elif token == "--block":
            flags["action"] = "block"
        elif token in {"--path", "--url", "--all", "--command"}:
            flags["match_on"] = token[2:]
        elif token in {"--why", "--message", "-m"}:
            index += 1
            if index >= len(tokens):
                error = f"{token} needs a sentence after it"
                break
            flags["message"] = tokens[index]
        elif token.startswith("--"):
            error = f"unknown option {token}"
            break
        else:
            positionals.append(token)
        index += 1
    return positionals, flags, error


def _add(text: str) -> str:
    positionals, flags, error = _parse_flags(_split(text))
    if error:
        return f"{error}\n\n{_HELP}"
    if not positionals:
        return "Usage: /rules add <regex> [tool] [--global] [--warn|--approve] [--why \"...\"]"
    tool = "terminal"
    if len(positionals) >= 2 and _TOOLISH.match(positionals[1]):
        tool = positionals[1]
        leftovers = positionals[2:]
        if leftovers:
            return (
                "Unexpected extra arguments: " + " ".join(leftovers) + "\n"
                "Quote the pattern if it contains spaces: /rules add \"git push --force\""
            )
        pattern = positionals[0]
    else:
        pattern = " ".join(positionals)

    settings = _settings()
    project = rules.project_root() if flags["scope"] == "project" else None
    rule, note = rules.add_rule(
        settings,
        pattern=pattern,
        tool=tool,
        action=flags["action"],
        scope=flags["scope"],
        message=flags["message"],
        match_on=flags["match_on"],
        project=project,
    )
    if rule is None:
        return f"Rule not added: {note}"
    if note:
        return f"{note}: {rule.id} — {rule.name}"
    return (
        f"Rule {rule.id} added ({rule.action}, {rule.tool}, match on {rule.match_on}, {rule.where}).\n"
        f"  pattern: {rule.pattern}\n"
        f"  why: {rule.explains}\n"
        f"  file: {rule.file}\n"
        f"  checked before every matching tool call, now and in future sessions.\n"
        f"  test: /rules test {rule.tool} <text>   ·   undo: /rules remove {rule.id}"
    )


def _hits() -> str:
    items = rules.recent_hits(20)
    if not items:
        return "No rule has fired yet. Rules are checked before every tool call; warn rules log here without blocking."
    lines = [f"last {len(items)} rule hit(s), newest first:"]
    for item in items:
        lines.append(
            f"  {str(item.get('rule_id'))} · {str(item.get('tool'))} · {str(item.get('action'))} · "
            f"\"{str(item.get('matched'))[:90]}\""
        )
    return "\n".join(lines)


def handle(raw_args: str = "", **kwargs: Any) -> str:
    text = (raw_args or "").strip()
    lowered = text.lower()

    if lowered in {"", "status"}:
        settings = _settings()
        project_dir = rules.project_root()
        rule_set, problems = rules.load_rules(settings, project_dir)
        project_count = sum(1 for rule in rule_set if rule.scope == "project")
        lines = [
            f"house_rules · {len(rule_set)} rule(s): {project_count} project, "
            f"{len(rule_set) - project_count} global",
            f"project: {project_dir} ({'read' if settings.project_file_enabled else 'ignored'})",
            f"enforcement: {'on' if settings.enabled else 'OFF'} · default action {settings.default_action}"
            + (" · warn blocks" if settings.warn_blocks else ""),
        ]
        last = rules.recent_hits(1)
        if last:
            item = last[0]
            lines.append(
                f"last hit: {str(item.get('rule_id'))} on {str(item.get('tool'))} — "
                f"\"{str(item.get('matched'))[:70]}\""
            )
        for problem in problems:
            lines.append(f"! {problem}")
        lines.append("")
        lines.append("add: /rules add <regex> [tool]   ·   list: /rules list   ·   help: /rules help")
        return "\n".join(lines)

    if lowered.startswith("list"):
        parts = text.split()
        scope = parts[1].lower() if len(parts) > 1 else "all"
        if scope not in {"all", "project", "global"}:
            return "Usage: /rules list [project|global]"
        return tools.render_rule_list(scope)

    if lowered.startswith("add"):
        return _add(text[3:].strip())

    if lowered.startswith("remove") or lowered.startswith("rm "):
        rest = text[6:].strip() if lowered.startswith("remove") else text[3:].strip()
        if not rest:
            return "Usage: /rules remove <id>   (ids look like hr_1a2b3c4d; see /rules list)"
        removed, note = rules.remove_rule(_settings(), rest.split()[0])
        if removed is None:
            return note
        return f"Removed {removed.id} — {removed.name} ({removed.where}). It no longer applies."

    if lowered.startswith("test"):
        rest = text[4:].strip()
        if not rest:
            return "Usage: /rules test <tool> <text>   e.g. /rules test terminal \"pip install requests\""
        head, _, tail = rest.partition(" ")
        tool = head.strip()
        args_text = tail.strip()
        if not args_text:
            return "Usage: /rules test <tool> <text>   (the text the pattern should match)"
        return tools.render_rule_test(tool, args_text, _settings())

    if lowered in {"hits", "log"}:
        return _hits()

    if lowered in {"help", "?", "-h", "--help"}:
        return _HELP

    return f"Unknown /rules subcommand: {text.split()[0]}\n\n{_HELP}"
