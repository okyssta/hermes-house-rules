"""Tool schemas.

Descriptions are written for a model that has never heard of this plugin: they
say what a rule *is*, that a rule outranks its own judgement, and how to add one
that will still be correct in a month. All four tools are cheap, local and
read-only apart from ``rule_add`` / ``rule_remove``.
"""

from __future__ import annotations

RULE_ADD = {
    "name": "rule_add",
    "description": (
        "Store a standing rule the user just taught you, so it is enforced before the matching "
        "tool call instead of being remembered as advice. Use it when the user says 'always', "
        "'never', 'from now on', 'don't', 'stop doing X' about how you use tools — e.g. 'use uv, "
        "not pip', 'never push to main', 'don't touch prod', 'ask before deleting'. The pattern is "
        "a Python regular expression matched with re.search against the tool call's command, path "
        "or URL text. Prefer scope='project' (the rule lands in <project>/.hermes/rules.json and "
        "travels with the repo) unless the correction is about this machine everywhere. Returns "
        "the rule id; tell the user that id and that the rule is checked before every tool call "
        "from now on. Write a specific, anchored pattern (r'\\bpip3?\\s+install\\b'), not a bare "
        "word that will also match harmless calls."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": (
                    "Python regex, matched with re.search (max 500 chars). Example: "
                    "'\\\\bpip3?\\\\s+install\\\\b' matches 'pip install x' and 'python -m pip install x'."
                ),
            },
            "tool": {
                "type": "string",
                "description": (
                    "Tool this rule applies to, e.g. 'terminal', 'write_file', 'patch', 'web_fetch'. "
                    "Use '*' to apply to every tool. Default 'terminal'."
                ),
            },
            "action": {
                "type": "string",
                "enum": ["block", "approve", "warn"],
                "description": (
                    "block = refuse the call and tell the model why; approve = escalate to the "
                    "human approval gate; warn = let it run and record a hit. Default 'block'."
                ),
            },
            "scope": {
                "type": "string",
                "enum": ["project", "global"],
                "description": (
                    "project = stored in the nearest git root's .hermes/rules.json and only applies "
                    "there; global = stored in the Hermes profile and applies everywhere. "
                    "Default 'project'."
                ),
            },
            "message": {
                "type": "string",
                "description": (
                    "Why the rule exists and what to do instead, in one sentence — this is what the "
                    "model reads when the rule blocks it. Example: 'pip installs into the system "
                    "interpreter; use `uv add` so the project venv stays in sync.'"
                ),
            },
            "name": {
                "type": "string",
                "description": "Short human name for the rule. Defaults to the message or the pattern.",
            },
            "match_on": {
                "type": "string",
                "enum": ["command", "path", "url", "all"],
                "description": (
                    "Which argument text the pattern is matched against: 'command' (command/cmd/"
                    "script), 'path' (path/file_path/directory/target...), 'url' (url/uri/endpoint), "
                    "or 'all' (command/path/url text plus the whole call as JSON). Default 'command'."
                ),
            },
        },
        "required": ["pattern"],
    },
}

RULE_LIST = {
    "name": "rule_list",
    "description": (
        "List the standing rules that are enforced before tool calls, in the order they are checked "
        "(project first, newest first), with each rule's id, action, tool, match mode, hit count and "
        "explanation. Use it when the user asks what rules exist, why something was blocked, or "
        "before adding a rule that might duplicate one. Shows broken entries and unreadable files "
        "rather than hiding them."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["all", "project", "global"],
                "description": "Which rules to show. Default 'all'.",
            }
        },
        "required": [],
    },
}

RULE_REMOVE = {
    "name": "rule_remove",
    "description": (
        "Delete a standing rule by its id (hr_xxxxxxxx) from whichever file owns it. Use it when the "
        "user retires or corrects a rule, or when a rule is blocking legitimate work. The previous "
        "file is kept beside it as rules.json.bak. Confirm the id first with rule_list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "rule_id": {
                "type": "string",
                "description": "The rule id shown by rule_list or returned by rule_add, e.g. hr_1a2b3c4d.",
            }
        },
        "required": ["rule_id"],
    },
}

RULE_TEST = {
    "name": "rule_test",
    "description": (
        "Check what the standing rules would do with a tool call, without running anything. Reports "
        "every rule, whether it matched, the exact text it matched, why, and the final decision "
        "(pass / warn / approve / block). Use it before adding a rule (does the pattern catch the "
        "call the user means, and not the harmless one?), and when the user asks 'would that be "
        "blocked?'. No tool call is made and no hit is recorded."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tool": {
                "type": "string",
                "description": "Tool name to test against, e.g. 'terminal' or 'write_file'. Default 'terminal'.",
            },
            "args_text": {
                "type": "string",
                "description": (
                    "The command, path or URL to check — the text the rule's pattern is matched "
                    "against, e.g. 'pip install requests' or '/etc/prod/config.yaml'."
                ),
            },
        },
        "required": ["tool", "args_text"],
    },
}

ALL = (RULE_ADD, RULE_LIST, RULE_REMOVE, RULE_TEST)
