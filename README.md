# house_rules

**Your corrections, enforced — not remembered.**

A Hermes Agent plugin that turns "use uv, not pip" into a rule that is checked
before every tool call. MIT, standard library only, no network calls.

---

## The problem

Every Hermes user repeats the same handful of corrections:

> use uv, not pip · never push to main · don't touch prod · ask before deleting

Memory may recall them. Recall is a suggestion, and a model twenty tool calls into
a task can talk itself past a suggestion. Nothing in the loop says *this exact
call is the thing the user told you not to do.*

`house_rules` says it. Rules are explicit, user-owned, and evaluated in the public
`pre_tool_call` hook **before the tool runs**:

- **block** — the call is refused and the model gets a message naming the rule,
  quoting the matched text, explaining why, and saying how to change the rule;
- **approve** — the call is escalated to the human approval gate (the same gate
  Hermes uses for dangerous commands), with the rule as its reason;
- **warn** — a hit is recorded and the call proceeds (unless `warn_blocks` is on).

## Quickstart

```bash
hermes plugins install okyssta/hermes-house-rules
hermes plugins enable house_rules
```

Until the repository is published and catalogued, install from a checkout by
linking the directory into your Hermes home's `plugins/` folder and enabling it:

```bash
ln -s "$PWD" ~/.hermes/plugins/house_rules   # profile homes use the same layout
hermes plugins enable house_rules
```

Then just tell the agent, in your own words:

> "From now on use `uv add`, never `pip install`."

The bundled skill teaches it to write a precise regex, test it, store it in the
project, and tell you the rule id. Or do it yourself:

```
/rules add "\bpip3?\s+install\b" terminal --why "use uv add so the venv stays in sync"
/rules list
/rules test terminal "pip install requests"     # → BLOCK, by the id printed above
/rules remove <id>
```

A rule can also be committed to a repository, which is the point of project scope:

```json
{
  "version": 1,
  "rules": [
    {
      "name": "pip install is blocked, use uv add",
      "pattern": "\\bpip3?\\s+install\\b",
      "tool": "terminal",
      "action": "block",
      "match_on": "command",
      "message": "pip installs into whichever interpreter happens to be active; use `uv add <package>` so the project venv and lockfile stay in sync."
    }
  ]
}
```

## What it looks like when it fires

Real output of `scripts/demo.py` (it builds a temp project, writes the rule above
to `.hermes/rules.json`, then drives the actual hook — no Hermes process, no
network):

```
project : /tmp/house-rules-demo/project
rules   : /tmp/house-rules-demo/project/.hermes/rules.json
hermes  : /tmp/house-rules-demo/hermes-home
rule    : pip install is blocked, use uv add  (hr_1afb21aa)

The file on disk:
{
  "version": 1,
  "rules": [
    {
      "name": "pip install is blocked, use uv add",
      "pattern": "\\bpip3?\\s+install\\b",
      "tool": "terminal",
      "action": "block",
      "match_on": "command",
      "message": "pip installs into whichever interpreter happens to be active; use `uv add <package>` so the project venv and lockfile stay in sync."
    }
  ]
}

──────────────────────────────────────────────────────────────────────────────
1. the agent tries: terminal(command='pip install requests')
──────────────────────────────────────────────────────────────────────────────
hook returned: action='block' (a block directive — the tool never ran)

message the model receives (verbatim):

    house_rules blocked this tool call — rule hr_1afb21aa "pip install is blocked, use uv add"
      matched in command: "pip install"
      why: pip installs into whichever interpreter happens to be active; use `uv add <package>` so the project venv and lockfile stay in sync.
      rule: pattern `\bpip3?\s+install\b` · tool terminal · scope project (/tmp/house-rules-demo/project) · action block
      in context: pip install requests
      To change it: /rules remove hr_1afb21aa — or edit /tmp/house-rules-demo/project/.hermes/rules.json

──────────────────────────────────────────────────────────────────────────────
2. the agent tries the thing the rule asks for: terminal(command='uv add requests')
──────────────────────────────────────────────────────────────────────────────
hook returned: None — no rule matched, the call proceeds.

──────────────────────────────────────────────────────────────────────────────
3. rule_test: would `pip install -r requirements.txt` be stopped?
──────────────────────────────────────────────────────────────────────────────
rule_test · tool=terminal · text='pip install -r requirements.txt'
checked 1 rule(s): project first, then global, newest first

✓ MATCH     hr_1afb21aa [block] project · tool terminal · command · \bpip3?\s+install\b
    matched in command: "pip install"
    why: pip installs into whichever interpreter happens to be active; use `uv add <package>` so the project venv and lockfile stay in sync.

decision: BLOCK — rule hr_1afb21aa would stop this call.
nothing was executed and no hit was recorded.

──────────────────────────────────────────────────────────────────────────────
4. the hit log — what actually fired (rule_test did not count)
──────────────────────────────────────────────────────────────────────────────
last 1 rule hit(s), newest first:
  hr_1afb21aa · terminal · block · "pip install"

──────────────────────────────────────────────────────────────────────────────
/rules — status as the user sees it
──────────────────────────────────────────────────────────────────────────────
house_rules · 1 rule(s): 1 project, 0 global
project: /tmp/house-rules-demo/project (read)
enforcement: on · default action block
last hit: hr_1afb21aa on terminal — "pip install"

add: /rules add <regex> [tool]   ·   list: /rules list   ·   help: /rules help

temp project kept for inspection: /tmp/house-rules-demo
```

Reproduce it:

```bash
python3 scripts/demo.py
# or, for a stable path in the output:
HOUSE_RULES_DEMO_DIR=/tmp/house-rules-demo python3 scripts/demo.py
```

## Tools

| Tool | What it does |
| --- | --- |
| `rule_add(pattern, tool="terminal", action="block", scope="project", message="", name=None, match_on="command")` | Store a standing rule. `pattern` is a Python regex (max 500 chars), matched with `re.search`. Returns the rule including its id. Re-adding the same rule is a no-op that returns the existing id. |
| `rule_list(scope="all")` | Every rule in policy order with id, action, tool, match mode, hit count and explanation — plus any broken entry or unreadable file, rather than hiding it. |
| `rule_remove(rule_id)` | Delete a rule from whichever file owns it. The previous file is kept as `rules.json.bak`. |
| `rule_test(tool, args_text)` | Would this call be blocked? Reports every rule, whether it matched, the exact matched text and why, and the final decision. Runs nothing and records no hit. |

`match_on` decides which argument text the pattern sees:

| `match_on` | Arguments read |
| --- | --- |
| `command` (default) | `command`, `cmd`, `shell_command`, `script` |
| `path` | `path`, `file_path`, `filepath`, `filename`, `directory`, `dir`, `target`, `destination`, `source`, `cwd`, `workdir` |
| `url` | `url`, `uri`, `endpoint`, `link`, `href` |
| `all` | the command/path/url text above **and** the whole call as sorted JSON — for nested arguments and MCP tools |

`/rules` mirrors all four verbs for a person at the keyboard:

```
/rules                                   status
/rules list [project|global]             every rule, in check order
/rules add <regex> [tool] [--global] [--warn|--approve|--block]
            [--path|--url|--all] [--why "sentence"]
/rules remove <id>
/rules test <tool> <text>
/rules hits                              the last recorded firings
```

## How a call is decided

1. The plugin's own tools (`rule_add`, `rule_list`, `rule_remove`, `rule_test`) are
   never subject to rules — otherwise a rule could make itself impossible to remove.
2. Rules are loaded from the project (`<nearest git root>/.hermes/rules.json`, if
   `project_file_enabled`) and the profile
   (`<hermes_home>/plugin-data/house_rules/rules.json`).
3. They are ordered **project first, then global; newest first within each group**,
   and every applicable rule is evaluated.
4. The strongest *effective* action wins: **block > approve > warn**. Within one
   action, the first rule in that order is the one reported. So a project rule that
   blocks is never softened by an older global rule that warns.
5. `warn_blocks = true` turns every `warn` rule into a block at evaluation time —
   tighten all your warnings at once without editing each rule.
6. A rule that matches records a hit: a counter always, and the matched substring
   (never the whole command) in a 200-entry recent log when `log_hits` is on.

Rule ids look like `hr_1afb21aa` and are a stable hash of the rule's identity
(scope, project, tool, match mode, pattern), so a hand-written entry has the same
id after every reload without anyone having to write one down.

## Configuration

`plugins.entries.house_rules.settings.<key>` — mirrored one-for-one in
`plugin.yaml` and `settings.py`:

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Evaluate rules before tool calls. Off: tools still work, nothing is enforced. |
| `default_action` | `"block"` | Action for a rule that does not carry one (hand-written entries). |
| `warn_blocks` | `false` | Treat every `warn` rule as a block. |
| `max_rules` | `200` | Refuse new rules beyond this many in one scope (1–1000). |
| `log_hits` | `true` | Keep the last 200 firings. Counters are kept either way. |
| `project_file_enabled` | `true` | Read `<project>/.hermes/rules.json`. Turn off to ignore rules that arrive with a repository. |

```bash
hermes config set plugins.entries.house_rules.settings.warn_blocks true
```

## house_rules is policy, not a firewall

This is **personal policy the user taught**, in the user's own words, about their
own machine. The threat model is a competent, well-meaning agent that forgot a
preference — not an adversary. That difference is the design:

- a rule is plain JSON the user can read, edit and delete, and every block message
  names the rule and the file it came from;
- a project's `.hermes/rules.json` can only *add* project-scoped rules — it cannot
  remove the user's global rules, claim global scope, or run code (patterns are
  compiled as regexes, capped at 500 characters, and matched against at most 8000
  characters of argument text per call);
- evaluation fails **open**: any unexpected error in the plugin returns `None` and
  the tool runs, because a personal-policy plugin must never wedge the agent;
- but a rule that matches is never silently skipped: a broken *entry* is skipped
  and reported, a broken file is reported, and neither disarms the rules around it
  (there is a test for the malformed-file and malformed-entry cases).

Security plugins answer a different question — *is this call dangerous?* — with
threat-intelligence graphs and deny-lists for dangerous commands, malicious
packages and prompt injection (**blackbox**), or by confining what a command can
reach (sandboxing tools of the **airlock** kind). They are built to constrain code
and requests the user did not write; `house_rules` is built to remember what the
user *did* say. They compose: a sandbox can confine a command while house_rules
refuses one, and neither needs to know about the other. house_rules does not
sandbox anything, does not scan for secrets, and does not pretend a regex is a
security boundary.

## Limits, stated plainly

- **Text, not meaning.** Rules see the arguments of a tool call. A command
  assembled inside a shell script, a URL fetched by a program, or an action taken
  through a tool with unfamiliar argument names will not be seen. `match_on="all"`
  widens the net to the whole JSON call, not to the program's behaviour.
- **The project is the current working directory's git root.** A task that
  `cd`s elsewhere is subject to that project's rules, not the one you were in.
  Global rules always apply.
- **`warn` is quiet by design.** A warn rule returns `None` (the call proceeds)
  and leaves its evidence in `/rules` and `/rules hits`. If you want warnings to
  stop calls, set `warn_blocks`.
- **Regexes can be pathological.** Pattern length and input length are capped and
  compiled patterns are cached, which bounds the common cases; a deliberately
  catastrophic regex can still cost time. Rules are written by the person who owns
  the machine, so this is a footgun, not an attack surface.
- **Approval semantics are the host's.** `approve` returns
  `{"action": "approve", "message": ..., "rule_key": "house_rules:<id>"}`; what
  the approval UI offers to remember is decided by Hermes, not by this plugin.
- **No history rewriting.** Removing a rule does not remove the hits it recorded.

## Privacy and disclosure

- Reads: the project's `.hermes/rules.json` (when present) and the plugin's own
  data directory. Nothing else.
- Writes: `<hermes_home>/plugin-data/house_rules/rules.json`, `hits.json`, and —
  only when a rule is added with `scope="project"` — the project's
  `.hermes/rules.json`, written atomically via a temp file + `os.replace` after
  copying the previous file to `rules.json.bak`.
- Stores the *matched substring* in the hit log, not the whole command.
- Standard library only, no network calls, no credentials, no extra processes, no
  core overrides.

## Development

```bash
# tests (58)
PYTHONPATH=/root/candidates/house_rules:/root/.hermes/hermes-agent \
  /root/peripheral/.venv/bin/python -m pytest -q

# the catalog-admission gate Hermes itself runs
hermes plugins validate /root/candidates/house_rules

# the demo above
python3 scripts/demo.py
```

Beyond the unit tests, the plugin was loaded through Hermes' own plugin manager
in a throwaway `HERMES_HOME`: discovery loaded it, `pre_tool_call` fired through
`get_pre_tool_call_directive` (`block` for `pip install requests`, `None` for
`uv add requests`, `None` for its own `rule_list`), `/rules` appeared in the
plugin command table, and `rule_test` dispatched through the real tool registry.

Layout:

```
__init__.py      register(): 4 tools, /rules, pre_tool_call, bundled skill
settings.py      config_schema mirror + defensive coercion
rules.py         rule model, storage, ordering, evaluation, hit accounting
matching.py      regex compilation cache, candidate text, safe search
tools.py         the four tool handlers
commands.py      /rules
schemas.py       tool schemas
skills/house_rules/SKILL.md
tests/           conftest + manifest, rule and hook suites
scripts/demo.py  the run captured above
```

## License

MIT © kyssta
