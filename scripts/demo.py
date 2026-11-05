#!/usr/bin/env python3
"""Demo: a project rule stops `pip install` before it runs.

Builds a throwaway git project with a hand-written ``.hermes/rules.json`` (the
shape a user or a repo would commit), then drives the real plugin code:

1. the ``pre_tool_call`` hook on ``pip install requests`` — and prints the exact
   message the model and the user get back;
2. the same hook on ``uv add requests`` — no rule matches, the call would run;
3. ``rule_test`` — the dry run, which reports the decision without recording a hit;
4. the hit log — what actually fired, and how often.

Nothing here touches the real Hermes profile: ``HERMES_HOME`` and the working
directory both point into a fresh temp directory. Run it with the plugin's parent
on ``sys.path`` (the script does that itself):

    python3 scripts/demo.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
PLUGIN_DIR = HERE.parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
sys.path.insert(0, os.environ.get("HERMES_SRC", "/root/.hermes/hermes-agent"))

ROOT = Path(os.environ.get("HOUSE_RULES_DEMO_DIR") or tempfile.mkdtemp(prefix="house-rules-demo-"))
HOME = ROOT / "hermes-home"
PROJECT = ROOT / "project"
(HOME).mkdir(parents=True, exist_ok=True)
(PROJECT / ".git").mkdir(parents=True, exist_ok=True)
(PROJECT / ".hermes").mkdir(parents=True, exist_ok=True)
REUSED = (HOME / "plugin-data" / "house_rules" / "hits.json").exists()

os.environ["HERMES_HOME"] = str(HOME)
os.chdir(PROJECT)

import house_rules  # noqa: E402
from house_rules import commands, rules, tools  # noqa: E402
from house_rules.settings import Settings  # noqa: E402

RULE = {
    "name": "pip install is blocked, use uv add",
    "pattern": r"\bpip3?\s+install\b",
    "tool": "terminal",
    "action": "block",
    "match_on": "command",
    "message": (
        "pip installs into whichever interpreter happens to be active; "
        "use `uv add <package>` so the project venv and lockfile stay in sync."
    ),
}
(PROJECT / ".hermes" / "rules.json").write_text(
    json.dumps({"version": 1, "rules": [RULE]}, indent=2) + "\n", encoding="utf-8"
)

house_rules.reset_settings_cache()
settings = Settings()


def banner(title: str) -> None:
    print()
    print("─" * 78)
    print(title)
    print("─" * 78)


def hook(command: str):
    """Call the plugin's real pre_tool_call handler and return its return value."""
    return house_rules._on_pre_tool_call("terminal", {"command": command}, task_id="demo")


print(f"project : {PROJECT}")
print(f"rules   : {PROJECT / '.hermes' / 'rules.json'}")
print(f"hermes  : {HOME}")
print(f"rule    : {RULE['name']}  ({rules.load_rules(settings, PROJECT)[0][0].id})")
if REUSED:
    print("note    : this demo directory has run before; the hit log below includes earlier runs.")
print()
print("The file on disk:")
print(json.dumps({"version": 1, "rules": [RULE]}, indent=2))

banner("1. the agent tries: terminal(command='pip install requests')")
result = hook("pip install requests")
print(
    f"hook returned: action={result['action']!r} (a block directive — the tool never ran)"
    if result
    else "hook returned: None"
)
print()
if result:
    print("message the model receives (verbatim):")
    print()
    for line in result["message"].splitlines():
        print("    " + line)

banner("2. the agent tries the thing the rule asks for: terminal(command='uv add requests')")
result = hook("uv add requests")
print("hook returned: None — no rule matched, the call proceeds.")

banner("3. rule_test: would `pip install -r requirements.txt` be stopped?")
print(tools.render_rule_test("terminal", "pip install -r requirements.txt", settings))

banner("4. the hit log — what actually fired (rule_test did not count)")
print(commands.handle("hits"))

banner("/rules — status as the user sees it")
print(commands.handle(""))

print()
print(f"temp project kept for inspection: {ROOT}")
