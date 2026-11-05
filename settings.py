"""Settings resolution for ``house_rules``.

Every key here mirrors a ``config_schema`` entry in ``plugin.yaml`` one-for-one —
the Desktop settings form, ``hermes config set`` and this module must agree, and
``tests/test_manifest.py`` asserts the two key sets are identical. Values are
coerced defensively: a typo in ``config.yaml`` must never stop the plugin from
answering, and must never silently turn a block rule into decoration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PLUGIN_ID = "house_rules"

SCHEMA_KEYS = (
    "enabled",
    "default_action",
    "warn_blocks",
    "max_rules",
    "log_hits",
    "project_file_enabled",
)

ACTIONS = ("block", "approve", "warn")
_MATCH_MODES = ("command", "path", "url", "all")


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _action(value: Any, default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in ACTIONS else default


@dataclass(frozen=True)
class Settings:
    """Resolved plugin settings.

    ``default_action`` is the action applied to a rule that does not carry its own
    (a hand-written entry in ``.hermes/rules.json``, or a rule whose ``action`` is
    unreadable). It is *not* a blanket action for every call: a rule that matches
    decides on its own, and ``warn_blocks`` only escalates ``warn`` rules.
    """

    enabled: bool = True
    default_action: str = "block"
    warn_blocks: bool = False
    max_rules: int = 200
    log_hits: bool = True
    project_file_enabled: bool = True

    @classmethod
    def from_ctx(cls, ctx: Any) -> "Settings":
        get = getattr(ctx, "get_config", None)
        if not callable(get):  # pragma: no cover - older hosts
            return cls()

        def value(key: str, fallback: Any) -> Any:
            try:
                resolved = get(key, default=fallback)
            except TypeError:  # hosts whose get_config takes one argument
                resolved = get(key)
            return fallback if resolved is None else resolved

        return cls(
            enabled=_bool(value("enabled", True), True),
            default_action=_action(value("default_action", "block"), "block"),
            warn_blocks=_bool(value("warn_blocks", False), False),
            max_rules=_int(value("max_rules", 200), 200, 1, 1000),
            log_hits=_bool(value("log_hits", True), True),
            project_file_enabled=_bool(value("project_file_enabled", True), True),
        )

    def describe(self) -> str:
        return "\n".join(
            [
                f"enabled: {self.enabled}",
                f"default action for rules without one: {self.default_action}",
                f"warn blocks when warn_blocks is on: {self.warn_blocks}",
                f"max rules per scope: {self.max_rules}",
                f"hit log: {'on (last 200)' if self.log_hits else 'off (counters only)'}",
                f"project files (.hermes/rules.json): {'read' if self.project_file_enabled else 'ignored'}",
            ]
        )
