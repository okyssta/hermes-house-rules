"""The pre_tool_call hook: directives, fail-open behaviour, and its blind spots."""

from __future__ import annotations

import pytest

import house_rules
from house_rules import matching, rules
from house_rules.settings import Settings


@pytest.fixture()
def hook_env(env, settings, monkeypatch):
    """A project rule that blocks pip, with settings injected into the hook."""
    rules.add_rule(
        settings,
        pattern=r"\bpip3?\s+install\b",
        message="pip writes into the system interpreter; use `uv add` instead.",
        name="pip install is blocked, use uv add",
        project=env.project,
    )
    monkeypatch.setattr(house_rules, "current_settings", lambda: settings)
    return env


def test_block_returns_a_message_that_names_the_rule_and_the_way_out(hook_env):
    result = house_rules._on_pre_tool_call("terminal", {"command": "pip install requests"})
    assert isinstance(result, dict)
    assert result["action"] == "block"
    message = result["message"]
    assert "house_rules blocked this tool call" in message
    assert "pip install is blocked, use uv add" in message
    assert '"pip install"' in message          # the matched text, quoted
    assert "use `uv add` instead" in message   # the human explanation
    assert "/rules remove hr_" in message      # how to change the rule
    assert ".hermes/rules.json" in message     # where it lives
    assert "rule_key" not in result            # block directives carry no rule_key


def test_a_blocking_rule_records_a_hit(hook_env):
    house_rules._on_pre_tool_call("terminal", {"command": "pip install requests"})
    loaded, _ = rules.load_rules(Settings(), hook_env.project)
    assert rules.hit_count(loaded[0].id) == 1
    recent = rules.recent_hits(1)
    assert recent[0]["tool"] == "terminal"
    assert recent[0]["matched"] == "pip install"


def test_approve_returns_an_approval_with_a_stable_rule_key(hook_env, monkeypatch):
    rules.add_rule(
        Settings(),
        pattern=r"\bgit\s+push\b",
        action="approve",
        tool="terminal",
        message="pushing is the user's call",
        project=hook_env.project,
    )
    result = house_rules._on_pre_tool_call("terminal", {"command": "git push origin main"})
    assert result is not None
    assert result["action"] == "approve"
    assert result["rule_key"].startswith("house_rules:hr_")
    assert "wants your approval" in result["message"]
    assert "git push" in result["message"]


def test_warn_returns_none_and_still_counts_the_hit(env, settings, monkeypatch):
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": r"\bnpm\s+install\b", "action": "warn", "tool": "terminal", "message": "prefer pnpm"},
    ]})
    monkeypatch.setattr(house_rules, "current_settings", lambda: settings)
    assert house_rules._on_pre_tool_call("terminal", {"command": "npm install left-pad"}) is None
    assert rules.hit_count(rules.load_rules(settings, env.project)[0][0].id) == 1


def test_warn_blocks_turns_a_warn_rule_into_a_block(env, monkeypatch):
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": r"\bnpm\s+install\b", "action": "warn", "tool": "terminal"},
    ]})
    strict = Settings(warn_blocks=True)
    monkeypatch.setattr(house_rules, "current_settings", lambda: strict)
    result = house_rules._on_pre_tool_call("terminal", {"command": "npm install left-pad"})
    assert result is not None and result["action"] == "block"
    assert "warn_blocks is on" in result["message"]


def test_plugin_tools_are_never_blocked(env, settings, monkeypatch):
    env.write(env.global_rules, {"version": 1, "rules": [
        {"pattern": r".*", "action": "block", "tool": "*", "match_on": "all"},
    ]})
    monkeypatch.setattr(house_rules, "current_settings", lambda: settings)
    for name in sorted(rules.PLUGIN_TOOLS):
        assert house_rules._on_pre_tool_call(name, {"pattern": "x", "rule_id": "hr_x"}) is None
    # The catch-all rule really is live for everything else.
    assert house_rules._on_pre_tool_call("terminal", {"command": "ls"}) is not None


def test_disabled_means_no_directive_but_rules_remain(env, settings, monkeypatch):
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": r"\bpip\b", "action": "block", "tool": "terminal"},
    ]})
    off = Settings(enabled=False)
    monkeypatch.setattr(house_rules, "current_settings", lambda: off)
    assert house_rules._on_pre_tool_call("terminal", {"command": "pip install x"}) is None
    assert len(rules.load_rules(off, env.project)[0]) == 1


def test_the_hook_fails_open_when_evaluation_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(rules, "evaluate", boom)
    monkeypatch.setattr(house_rules, "current_settings", lambda: Settings())
    assert house_rules._on_pre_tool_call("terminal", {"command": "pip install x"}) is None


def test_the_hook_survives_odd_arguments(env, settings, monkeypatch):
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": r"\bpip\b", "action": "block", "tool": "terminal", "match_on": "all"},
    ]})
    monkeypatch.setattr(house_rules, "current_settings", lambda: settings)
    assert house_rules._on_pre_tool_call("terminal", None) is None
    assert house_rules._on_pre_tool_call("terminal", "not a dict") is None
    assert house_rules._on_pre_tool_call("", {"command": "pip install x"}) is None
    assert house_rules._on_pre_tool_call("terminal", {"command": "pip install x"}) is not None


def test_a_project_rule_does_not_follow_the_agent_to_another_directory(env, settings, monkeypatch, tmp_path):
    rules.add_rule(settings, pattern=r"\bpip\s+install\b", project=env.project)
    monkeypatch.setattr(house_rules, "current_settings", lambda: settings)
    assert house_rules._on_pre_tool_call("terminal", {"command": "pip install x"}) is not None

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert house_rules._on_pre_tool_call("terminal", {"command": "pip install x"}) is None

    # A global rule still applies there.
    rules.add_rule(settings, pattern=r"\bpip\s+install\b", scope="global")
    assert house_rules._on_pre_tool_call("terminal", {"command": "pip install x"}) is not None


def test_the_matched_field_is_reported(env, settings, monkeypatch):
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": r"/etc/prod/", "action": "block", "tool": "read_file", "match_on": "path",
         "message": "prod config is off limits"},
    ]})
    monkeypatch.setattr(house_rules, "current_settings", lambda: settings)
    result = house_rules._on_pre_tool_call("read_file", {"file_path": "/etc/prod/db.yaml"})
    assert result is not None and result["action"] == "block"
    assert "matched in path" in result["message"]
    assert "/etc/prod/" in result["message"]


def test_matching_never_touches_an_external_file(env, settings, monkeypatch):
    """A pattern that looks like shell or Python is still only a regex."""
    evil = "'; import os; os.system('touch /tmp/pwned'); '"
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": evil, "action": "block", "tool": "terminal"},
    ]})
    monkeypatch.setattr(house_rules, "current_settings", lambda: settings)
    # It compiles as a regex and simply does not match a normal command.
    assert matching.compile_pattern(evil) is not None
    assert house_rules._on_pre_tool_call("terminal", {"command": "ls -la"}) is None
