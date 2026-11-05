"""Rule storage, ordering, matching and the four tool handlers."""

from __future__ import annotations

import json
from pathlib import Path

from house_rules import matching, rules, tools
from house_rules.settings import Settings


def _terminal(text: str) -> dict:
    return {"command": text}


def test_add_rule_writes_the_project_file_and_returns_a_stable_id(env, settings):
    rule, note = rules.add_rule(
        settings,
        pattern=r"\bpip3?\s+install\b",
        message="pip writes into the system interpreter; use `uv add`.",
        project=env.project,
    )
    assert note == ""
    assert rule is not None
    assert rules.RULE_ID_RE.match(rule.id)
    assert rule.scope == "project"
    assert rule.project == str(env.project)
    stored = env.read(env.project_rules)["rules"]
    assert [entry["id"] for entry in stored] == [rule.id]
    assert stored[0]["pattern"] == r"\bpip3?\s+install\b"
    assert stored[0]["message"].startswith("pip writes")

    # Same identity, same id: re-adding is a no-op, not a second rule.
    again, note = rules.add_rule(settings, pattern=r"\bpip3?\s+install\b", project=env.project)
    assert again is not None and again.id == rule.id
    assert "already exists" in note
    assert len(env.read(env.project_rules)["rules"]) == 1


def test_add_rule_refuses_an_invalid_or_oversized_pattern(env, settings):
    rule, note = rules.add_rule(settings, pattern="(", project=env.project)
    assert rule is None and "not a valid regular expression" in note
    rule, note = rules.add_rule(settings, pattern="a" * (matching.MAX_PATTERN_CHARS + 1), project=env.project)
    assert rule is None and "longer than" in note
    rule, note = rules.add_rule(settings, pattern="   ", project=env.project)
    assert rule is None and "needs a pattern" in note
    # Nothing was written for a refused rule.
    assert not env.project_rules.exists()


def test_add_rule_rejects_bad_scope_action_and_match_mode(env, settings):
    assert "scope must be" in rules.add_rule(settings, pattern="x", scope="galaxy")[1]
    assert "action must be" in rules.add_rule(settings, pattern="x", action="explode")[1]
    assert "match_on must be" in rules.add_rule(settings, pattern="x", match_on="vibes")[1]


def test_global_scope_lives_in_the_profile_not_the_project(env, settings):
    rule, note = rules.add_rule(
        settings, pattern=r"\bsudo\s+rm\s+-rf\b", scope="global", tool="*", match_on="all"
    )
    assert note == "" and rule is not None
    assert rule.scope == "global" and rule.where == "global"
    assert env.global_rules.is_file()
    assert not env.project_rules.exists()
    assert env.read(env.global_rules)["rules"][0]["id"] == rule.id


def test_hand_written_rules_get_a_stable_id_and_defaults(env, settings):
    env.write(
        env.global_rules,
        {"version": 1, "rules": [{"pattern": r"\bgit\s+push\b", "message": "no pushes from here"}]},
    )
    first, _ = rules.load_rules(settings, env.project)
    second, _ = rules.load_rules(settings, env.project)
    assert len(first) == 1
    assert first[0].id == second[0].id
    assert first[0].tool == "*"          # absent tool means every tool
    assert first[0].action == ""         # resolved from settings at evaluation time
    assert first[0].name == "no pushes from here"


def test_rule_list_shows_ids_scopes_and_hits(env, settings):
    project_rule, _ = rules.add_rule(
        settings, pattern=r"\bpip\s+install\b", message="use uv", project=env.project
    )
    rules.add_rule(settings, pattern=r"\bdropdb\b", scope="global", project=env.project)
    rules.evaluate("terminal", _terminal("pip install requests"), settings, env.project)

    listed = tools.render_rule_list("all", settings, env.project)
    assert project_rule.id in listed
    assert "project rules" in listed and "global rules" in listed
    assert "use uv" in listed
    assert "hits 1" in listed
    assert "recent hits" in listed


def test_remove_rule_deletes_it_from_the_file_that_owns_it(env, settings):
    project_rule, _ = rules.add_rule(settings, pattern=r"\bpip\s+install\b", project=env.project)
    global_rule, _ = rules.add_rule(settings, pattern=r"\bdropdb\b", scope="global")

    removed, note = rules.remove_rule(settings, project_rule.id, env.project)
    assert note == "" and removed is not None and removed.id == project_rule.id
    assert env.read(env.project_rules)["rules"] == []
    assert env.read(env.global_rules)["rules"][0]["id"] == global_rule.id

    missing, note = rules.remove_rule(settings, "hr_deadbeef", env.project)
    assert missing is None and "no rule with id" in note
    bad, note = rules.remove_rule(settings, "not-an-id", env.project)
    assert bad is None and "hr_1a2b3c4d" in note


def test_project_rules_are_checked_before_global_rules(env, settings):
    env.write(
        env.project_rules,
        {"version": 1, "rules": [
            {"pattern": r"\bpip\b", "action": "block", "tool": "terminal", "created_at": 1.0},
        ]},
    )
    env.write(
        env.global_rules,
        {"version": 1, "rules": [
            {"pattern": r"\bpip\b", "action": "block", "tool": "terminal", "created_at": 99.0},
        ]},
    )
    decision = rules.evaluate("terminal", _terminal("pip install x"), settings, env.project)
    assert decision is not None
    assert decision.rule.scope == "project"
    assert decision.rule.created_at == 1.0  # older, but project wins


def test_newest_rule_wins_within_a_scope(env, settings):
    env.write(
        env.project_rules,
        {"version": 1, "rules": [
            {"pattern": r"\bdeploy\b", "action": "warn", "tool": "terminal", "created_at": 1.0},
            {"pattern": r"\bdeploy\s+prod\b", "action": "warn", "tool": "terminal", "created_at": 2.0},
        ]},
    )
    decision = rules.evaluate("terminal", _terminal("deploy prod now"), settings, env.project)
    assert decision is not None
    assert decision.rule.created_at == 2.0
    rows, _ = rules.scan_rules("terminal", _terminal("deploy prod now"), settings, env.project)
    assert [row.rule.created_at for row in rows] == [2.0, 1.0]


def test_a_block_wins_over_an_approve_even_when_the_approve_is_newer(env, settings):
    env.write(
        env.project_rules,
        {"version": 1, "rules": [
            {"pattern": r"\brm\b", "action": "approve", "tool": "terminal", "created_at": 5.0},
            {"pattern": r"\brm\s+-rf\b", "action": "block", "tool": "terminal", "created_at": 1.0},
        ]},
    )
    decision = rules.evaluate("terminal", _terminal("rm -rf /tmp/x"), settings, env.project)
    assert decision is not None and decision.action == "block"


def test_command_rules_ignore_paths_and_path_rules_ignore_commands(env, settings):
    env.write(
        env.project_rules,
        {"version": 1, "rules": [
            {"pattern": r"pip\s+install", "action": "block", "tool": "*", "match_on": "command"},
            {"pattern": r"/etc/prod/", "action": "block", "tool": "*", "match_on": "path"},
        ]},
    )
    # A file whose *path* mentions pip is not a pip install command.
    assert rules.evaluate("write_file", {"path": "/tmp/pip install notes.md"}, settings, env.project) is None
    # A path rule sees `file_path`, `path` and friends.
    decision = rules.evaluate("read_file", {"file_path": "/etc/prod/config.yaml"}, settings, env.project)
    assert decision is not None and decision.rule.match_on == "path"
    # ...and a path rule does not fire on a command that merely mentions it.
    assert rules.evaluate("terminal", _terminal("echo /etc/prod/config.yaml"), settings, env.project) is None


def test_url_rules_match_the_url_argument(env, settings):
    env.write(
        env.project_rules,
        {"version": 1, "rules": [
            {"pattern": r"^https?://api\.stripe\.com/v1/charges", "action": "approve",
             "tool": "web_fetch", "match_on": "url"},
        ]},
    )
    assert rules.evaluate("web_fetch", {"url": "https://example.com"}, settings, env.project) is None
    decision = rules.evaluate(
        "web_fetch", {"url": "https://api.stripe.com/v1/charges"}, settings, env.project
    )
    assert decision is not None and decision.action == "approve"


def test_all_mode_sees_nested_arguments(env, settings):
    env.write(
        env.project_rules,
        {"version": 1, "rules": [
            {"pattern": r"prod-db\.internal", "action": "block", "tool": "mcp__db__query", "match_on": "all"},
        ]},
    )
    args = {"query": "select 1", "connection": {"host": "prod-db.internal", "port": 5432}}
    decision = rules.evaluate("mcp__db__query", args, settings, env.project)
    assert decision is not None and decision.action == "block"
    assert "prod-db.internal" in decision.hit.matched


def test_rule_test_reports_the_decision_without_recording_a_hit(env, settings):
    rule, _ = rules.add_rule(
        settings,
        pattern=r"\bpip3?\s+install\b",
        message="use `uv add` instead",
        project=env.project,
    )
    report = tools.rule_test({"tool": "terminal", "args_text": "python -m pip install requests"})
    assert "MATCH" in report
    assert rule.id in report
    assert "use `uv add` instead" in report
    assert "decision: BLOCK" in report
    assert "no hit was recorded" in report
    assert rules.hit_count(rule.id) == 0

    passed = tools.rule_test({"tool": "terminal", "args_text": "uv add requests"})
    assert "decision: PASS" in passed
    assert "did not match" in passed

    empty = tools.rule_test({"tool": "terminal", "args_text": ""})
    assert "needs args_text" in empty


def test_hits_are_counted_and_the_recent_log_is_capped(env, settings, monkeypatch):
    rule, _ = rules.add_rule(settings, pattern=r"\bpip\s+install\b", project=env.project)
    monkeypatch.setattr(rules, "RECENT_HITS_KEPT", 3)
    for _ in range(5):
        rules.evaluate("terminal", _terminal("pip install requests"), settings, env.project)
    assert rules.hit_count(rule.id) == 5
    recent = rules.recent_hits(10)
    assert len(recent) == 3
    assert all(item["rule_id"] == rule.id for item in recent)
    assert recent[0]["matched"] == "pip install"

    env.write(env.hits, "{not json")
    assert rules.hit_count(rule.id) == 0  # unreadable log reads as empty, never raises
    assert rules.recent_hits(5) == []


def test_warn_rule_records_a_hit_and_lets_the_call_run(env, settings):
    env.write(
        env.project_rules,
        {"version": 1, "rules": [
            {"pattern": r"\bnpm\s+i(nstall)?\s+-g\b", "action": "warn", "tool": "terminal"},
        ]},
    )
    decision = rules.evaluate("terminal", _terminal("npm install -g yarn"), settings, env.project)
    assert decision is not None and decision.action == "warn"
    assert rules.hit_count(decision.rule.id) == 1


def test_rules_survive_a_reload(env, settings):
    rule, _ = rules.add_rule(
        settings, pattern=r"\bgit\s+push\b[^\n]*\bmain\b", message="never push to main", project=env.project
    )
    matching.clear_cache()
    fresh = Settings()
    loaded, problems = rules.load_rules(fresh, env.project)
    assert problems == []
    assert [item.id for item in loaded] == [rule.id]
    assert loaded[0].message == "never push to main"
    decision = rules.evaluate("terminal", _terminal("git push origin main"), fresh, env.project)
    assert decision is not None and decision.rule.id == rule.id
    # A different branch is not caught by this rule.
    assert rules.evaluate("terminal", _terminal("git push origin feature/x"), fresh, env.project) is None


def test_malformed_project_file_does_not_disarm_the_global_rules(env, settings):
    env.write(env.global_rules, {"version": 1, "rules": [
        {"pattern": r"\bpip\s+install\b", "action": "block", "tool": "terminal"},
    ]})
    env.write(env.project_rules, "{ this is not json ")
    decision = rules.evaluate("terminal", _terminal("pip install requests"), settings, env.project)
    assert decision is not None and decision.rule.scope == "global"
    _, problems = rules.load_rules(settings, env.project)
    assert any("unreadable" in problem for problem in problems)


def test_a_malformed_entry_does_not_disarm_the_other_entries(env, settings):
    env.write(env.global_rules, {"version": 1, "rules": [
        {"pattern": 12345, "action": "block"},
        {"not_a_rule": True},
        {"pattern": r"\bpip\s+install\b", "action": "block", "tool": "terminal"},
    ]})
    loaded, problems = rules.load_rules(settings, env.project)
    assert len(loaded) == 1
    assert len(problems) == 2
    decision = rules.evaluate("terminal", _terminal("pip install requests"), settings, env.project)
    assert decision is not None and decision.rule.pattern == r"\bpip\s+install\b"


def test_an_uncompilable_pattern_is_skipped_without_crashing(env, settings):
    env.write(env.global_rules, {"version": 1, "rules": [
        {"pattern": "(", "action": "block", "tool": "terminal", "id": "hr_00000000"},
        {"pattern": r"\bpip\s+install\b", "action": "block", "tool": "terminal"},
    ]})
    decision = rules.evaluate("terminal", _terminal("pip install requests"), settings, env.project)
    assert decision is not None and decision.rule.pattern == r"\bpip\s+install\b"
    rows, _ = rules.scan_rules("terminal", _terminal("pip install requests"), settings, env.project)
    statuses = {row.rule.id: row.status for row in rows}
    assert "bad-pattern" in statuses.values()


def test_project_rules_cannot_claim_global_scope(env, settings):
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": r"\bpip\b", "action": "block", "scope": "global", "tool": "terminal"},
    ]})
    loaded, _ = rules.load_rules(settings, env.project)
    assert loaded[0].scope == "project"


def test_project_file_reads_can_be_switched_off(env):
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": r"\bpip\b", "action": "block", "tool": "terminal"},
    ]})
    off = Settings(project_file_enabled=False)
    assert rules.load_rules(off, env.project)[0] == []
    assert rules.evaluate("terminal", _terminal("pip install x"), off, env.project) is None
    assert rules.evaluate("terminal", _terminal("pip install x"), Settings(), env.project) is not None


def test_max_rules_is_enforced(env):
    tight = Settings(max_rules=2)
    assert rules.add_rule(tight, pattern="aaa", project=env.project)[0] is not None
    assert rules.add_rule(tight, pattern="bbb", project=env.project)[0] is not None
    refused, note = rules.add_rule(tight, pattern="ccc", project=env.project)
    assert refused is None and "max_rules is 2" in note


def test_max_rules_never_hides_hand_written_rules_from_enforcement(env):
    entries = [
        {"pattern": rf"\bnoise{i}\b", "action": "block", "tool": "terminal"} for i in range(5)
    ]
    entries.append({"pattern": r"\bpip\b", "action": "block", "tool": "terminal"})
    env.write(env.project_rules, {"version": 1, "rules": entries})
    tight = Settings(max_rules=1)
    assert len(rules.load_rules(tight, env.project)[0]) == 6
    decision = rules.evaluate("terminal", _terminal("pip install x"), tight, env.project)
    assert decision is not None and decision.rule.pattern == r"\bpip\b"


def test_project_scope_is_refused_when_project_files_are_off(env):
    off = Settings(project_file_enabled=False)
    rule, note = rules.add_rule(off, pattern=r"\bpip\b", scope="project", project=env.project)
    assert rule is None and "project rule files are disabled" in note
    assert not env.project_rules.exists()


def test_a_rule_without_an_action_uses_the_default_action(env):
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": r"\brm\s+-rf\b", "tool": "terminal"},
    ]})
    approving = Settings(default_action="approve")
    decision = rules.evaluate("terminal", _terminal("rm -rf build"), approving, env.project)
    assert decision is not None and decision.action == "approve"
    assert decision.declared_action == "approve"


def test_warn_blocks_escalates_every_warn_rule(env):
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": r"\bnpm\b", "action": "warn", "tool": "terminal"},
    ]})
    escalating = Settings(warn_blocks=True)
    decision = rules.evaluate("terminal", _terminal("npm ci"), escalating, env.project)
    assert decision is not None
    assert decision.action == "block" and decision.escalated is True
    assert "warn_blocks is on" in rules.decision_message(decision)


def test_candidate_text_is_built_from_the_right_argument(env):
    texts = matching.candidate_texts(
        {"command": "pip install x", "path": "/tmp/a", "url": "https://e.x", "other": "zzz"}
    )
    assert "pip install x" in texts["command"]
    assert "/tmp/a" in texts["path"]
    assert "https://e.x" in texts["url"]
    assert any("zzz" in text for text in texts["all"])


def test_patterns_are_capped_and_compiled_once(monkeypatch):
    matching.clear_cache()
    first = matching.compile_pattern(r"\bpip\b")
    second = matching.compile_pattern(r"\bpip\b")
    assert first is second  # cache hit, not recompiled
    assert matching.compile_pattern("a" * (matching.MAX_PATTERN_CHARS + 1)) is None
    assert matching.compile_pattern(None) is None
    assert matching.compile_pattern("*bad") is None
    long_text = "x" * (matching.MAX_TEXT_CHARS + 500) + "pip install"
    # The tail beyond the cap is not scanned: the cap is real, and documented.
    assert matching.search(r"pip\s+install", (long_text,)) is None


def test_project_write_is_atomic_and_keeps_a_backup(env, settings):
    rules.add_rule(settings, pattern=r"\bfoo\b", project=env.project)
    first = env.project_rules.read_text(encoding="utf-8")
    rules.add_rule(settings, pattern=r"\bbar\b", project=env.project)

    backup = env.project_rules.with_name("rules.json.bak")
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == first
    assert sorted(path.name for path in (env.project / ".hermes").iterdir()) == [
        "rules.json",
        "rules.json.bak",
    ]
    payload = json.loads(env.project_rules.read_text(encoding="utf-8"))
    assert len(payload["rules"]) == 2


def test_rules_fire_for_any_tool_when_the_rule_says_so(env, settings):
    env.write(env.project_rules, {"version": 1, "rules": [
        {"pattern": r"\.env$", "action": "block", "tool": "read_file", "match_on": "path"},
    ]})
    assert rules.evaluate("read_file", {"path": "/srv/app/.env"}, settings, env.project) is not None
    assert rules.evaluate("terminal", _terminal("cat /srv/app/.env"), settings, env.project) is None
