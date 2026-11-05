"""house_rules — the user's standing corrections, enforced before the tool runs.

Every Hermes user repeats the same handful of corrections: *use uv, not pip*,
*never push to main*, *don't touch prod*, *ask before deleting*. Memory can recall
them — but recall is a suggestion, and a model deep in a task can talk itself past
a suggestion. This plugin stores those corrections as explicit, user-owned rules
and evaluates every tool call against them through the public ``pre_tool_call``
hook, so the call is stopped (or held for approval, or flagged) before anything
executes.

What it adds:

* **``rule_add`` / ``rule_list`` / ``rule_remove`` / ``rule_test``** — teach,
  inspect, retire and dry-run the rules.
* **``/rules``** — the same four verbs for a person at the keyboard.
* **A ``pre_tool_call`` hook** — project rules first, then global, newest first;
  ``block`` returns a message naming the rule, approving escalates to the human
  approval gate, ``warn`` records a hit and lets the call through.
* **A bundled skill** — teaches the agent to turn "always/never/from now on"
  into a precise regex at the moment the user says it.

This is personal policy the user taught, not a threat-intelligence firewall: the
rules are written by the person they constrain. State lives in
``<hermes_home>/plugin-data/house_rules/`` plus the project's own
``.hermes/rules.json``; no network, no credentials, no core overrides.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from . import commands, rules, schemas, tools
from .settings import PLUGIN_ID, SCHEMA_KEYS, Settings

logger = logging.getLogger(__name__)

_CTX: Any = None
_CACHED: Settings | None = None
_CACHED_AT = 0.0
_CACHE_SECONDS = 2.0


def current_settings() -> Settings:
    """Resolved settings, memoised briefly.

    ``ctx.get_config`` is cheap, but the hook runs on every tool call and a turn
    can make dozens; the memo keeps the hot path free while still noticing a
    settings change within the same conversation.
    """
    global _CACHED, _CACHED_AT

    now = time.monotonic()
    if _CACHED is not None and now - _CACHED_AT < _CACHE_SECONDS:
        return _CACHED
    if _CTX is None:
        _CACHED = Settings()
    else:
        try:
            _CACHED = Settings.from_ctx(_CTX)
        except Exception:  # pragma: no cover - a broken host must not disable policy
            logger.debug("house_rules: settings resolution failed", exc_info=True)
            _CACHED = Settings()
    _CACHED_AT = now
    return _CACHED


def reset_settings_cache() -> None:
    """Forget the memoised settings (tests, and after ``hermes config set``)."""
    global _CACHED, _CACHED_AT
    _CACHED = None
    _CACHED_AT = 0.0


# ─────────────────────────────────────────────────────────────── the hook


def _on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    task_id: str = "",
    **kwargs: Any,
) -> dict[str, str] | None:
    """Evaluate one tool call against the rules.

    Fails **open**: any exception in this plugin returns ``None`` so the tool runs.
    That is the right default for a personal-policy plugin — a bug here must not
    wedge the agent — and it is why the rule loader treats a broken entry as a
    skipped entry rather than a broken file: a malformed rule must never disarm
    the rules around it, and the only failure that reaches this handler as an
    exception is one the plugin cannot reason about at all.
    """
    try:
        settings = current_settings()
        if not settings.enabled:
            return None
        name = str(tool_name or "").strip()
        if not name or name in rules.PLUGIN_TOOLS:
            return None

        decision = rules.evaluate(name, args, settings)
        if decision is None:
            return None
        if decision.action == "warn":
            logger.warning(
                "house_rules: warn rule %s matched %s (%r) — call allowed",
                decision.rule.id,
                name,
                decision.hit.matched,
            )
            return None
        return {
            "action": decision.action,
            "message": rules.decision_message(decision),
            **({"rule_key": decision.rule_key} if decision.action == "approve" else {}),
        }
    except Exception:  # pragma: no cover - fail open, never wedge a turn
        logger.debug("house_rules: pre_tool_call evaluation failed", exc_info=True)
        return None


# ─────────────────────────────────────────────────────────────── register


def register(ctx: Any) -> None:
    global _CTX

    _CTX = ctx
    settings = current_settings()

    handlers = {
        "rule_add": tools.rule_add,
        "rule_list": tools.rule_list,
        "rule_remove": tools.rule_remove,
        "rule_test": tools.rule_test,
    }
    for schema in schemas.ALL:
        try:
            ctx.register_tool(
                name=schema["name"],
                toolset="house_rules",
                schema=schema,
                handler=handlers[schema["name"]],
            )
        except Exception:  # pragma: no cover - one bad surface must not kill the rest
            logger.warning("house_rules: could not register tool %s", schema["name"], exc_info=True)

    try:
        ctx.register_command(
            "rules",
            commands.handle,
            description="Standing rules enforced before every tool call",
            args_hint="[list|add <regex> [tool]|remove <id>|test <tool> <text>]",
        )
    except Exception:  # pragma: no cover - optional surface
        logger.warning("house_rules: could not register /rules", exc_info=True)

    try:
        ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    except Exception:  # pragma: no cover - a host without the hook
        logger.warning("house_rules: could not register the pre_tool_call hook", exc_info=True)

    skill_dir = Path(__file__).parent / "skills" / "house_rules"
    if (skill_dir / "SKILL.md").is_file():
        try:
            ctx.register_skill("house_rules", str(skill_dir))
        except Exception:  # pragma: no cover - optional surface
            logger.debug("house_rules: could not register bundled skill", exc_info=True)

    try:
        count = len(rules.load_rules(settings)[0])
    except Exception:  # pragma: no cover - listing is cosmetic at startup
        count = 0
    logger.info("house_rules ready: %s rule(s), enforcement %s", count, "on" if settings.enabled else "off")


__all__ = ["register", "current_settings", "reset_settings_cache", "PLUGIN_ID", "SCHEMA_KEYS", "rules"]
