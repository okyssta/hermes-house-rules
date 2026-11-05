---
name: house_rules
description: "Turn the user's standing corrections into rules enforced before every tool call."
version: 1.0.0
author: kyssta
license: MIT
metadata:
  hermes:
    tags: [policy, guardrails, rules, safety, workflow]
---

# House rules

The user has told you how they want tools used. `house_rules` stores those
corrections as rules that are evaluated **before** every tool call by the plugin's
`pre_tool_call` hook — so they hold even when you forget them, and in future
sessions where you never saw the original conversation.

## When to add a rule

Add one, without being asked to "remember" it, when the user states a standing
correction about how you work:

- "Use uv, not pip." → block `pip install`
- "Never push to main." → block `git push` that targets main
- "Don't touch prod." → block calls whose path or command names production
- "Ask before deleting anything." → **approve** `rm`, or `write_file` deletes
- "I don't want you installing global npm packages." → block `npm i -g`
- "Always run the tests before you say you're done." → this one is a workflow
  expectation, not a tool-call pattern; say so rather than inventing a rule that
  will misfire.

Do **not** add a rule for a one-off instruction ("use the staging key this time"),
for a preference that has no identifiable tool-call text ("be more concise"), or
for something the user did not say. A rule is the user's policy, not your guess at
it.

## How to add one

```
rule_add(pattern=r"\bpip3?\s+install\b", tool="terminal", action="block",
         scope="project", match_on="command",
         message="pip installs into the interpreter; use `uv add` so the project venv stays in sync.")
```

Then:

1. **Check for an existing rule first** — `rule_list()` — so you extend rather
   than duplicate, and so you can tell the user their rule already exists.
2. **Write a precise regex.** `re.search` semantics: it matches anywhere in the
   text, so anchor it. Prefer
   `\bpip3?\s+install\b` over `pip`; `git\s+push\b[^\n]*\b(origin\s+)?main\b`
   over `main`. Cap yourself well under 500 characters.
3. **Choose the field.** `match_on="command"` for shell commands,
   `"path"` for files/directories (covers `path`, `file_path`, `directory`,
   `target`...), `"url"` for fetches, `"all"` only when the thing you are matching
   could be in any argument.
4. **Choose the action.** `block` for "never do this"; `approve` for "ask me
   first" (this escalates to the real human approval gate, so it is the right
   choice for destructive-but-sometimes-legitimate calls); `warn` for "I want to
   know when this happens" — a warn rule records a hit and lets the call run
   unless the user has set `warn_blocks`.
5. **Choose the scope.** `scope="project"` (the default) stores the rule in
   `<nearest git root>/.hermes/rules.json`, where it travels with the repo and
   only applies there. Use `scope="global"` only when the correction is about
   this machine everywhere ("never `sudo rm -rf`").
6. **Write the `message`.** One sentence, addressed to your future self: why the
   rule exists and what to do instead. This is the text you will read at the
   moment you are blocked, so "use `uv add` so the project venv stays in sync" is
   useful and "user preference" is not.
7. **Test before you commit to it.** `rule_test(tool="terminal",
   args_text="pip install requests")` should say BLOCK, and
   `rule_test(tool="terminal", args_text="uv add requests")` should say PASS. If
   the harmless example also matches, the pattern is too broad — narrow it and
   test again.
8. **Tell the user the id** (`hr_1a2b3c4d`) and that the rule is checked before
   every matching tool call from now on, in this and future sessions, and that
   `/rules remove <id>` undoes it.

## What the user sees when a rule fires

A block returns a message naming the rule, quoting the matched text, explaining
why, and giving the id plus the file it lives in. That message is the tool result:
do not retry the same call, and do not look for a way around the rule. Change the
approach (use `uv add`), or ask the user whether the rule should change. Editing
`.hermes/rules.json` or removing a rule is the user's decision — offer it, do not
take it.

An `approve` rule means the call is paused for the human: explain what you are
about to run and why, and let them decide. A `warn` rule leaves no trace in the
conversation by design; `/rules` and `/rules hits` show what fired.

## The `/rules` command

`/rules` (status), `/rules list`, `/rules add <regex> [tool] [--global] [--warn|
--approve] [--why "..."]`, `/rules remove <id>`, `/rules test <tool> <text>`,
`/rules hits`. Reach for the tools rather than the command when you are the one
acting; reach for the command when you are telling the user what to type.

## Honest limits, so you do not oversell it

Rules match the text of a tool call, not its meaning: a command assembled inside a
script, a URL fetched by a program, or an action taken through an MCP tool with
different argument names will not be seen. Rules are the user's own policy and are
not a security boundary — a hostile repository's `.hermes/rules.json` can only add
rules (it cannot remove yours or run code), but it can make your work harder, so
mention a surprising project rule instead of silently working around it.
