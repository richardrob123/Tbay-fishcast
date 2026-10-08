#!/usr/bin/env python3
# harness-managed: refreshed from the harness plugin; do not edit this copy.
"""PreToolUse guard for Agent / Task / Workflow calls: enforces .claude/models.json.

Reads the hook JSON on stdin; exits 2 to block (message on stderr), 0 to allow.
Stdlib only. Does nothing when .claude/models.json is missing or has no `roles`.

It blocks only REAL model contradictions, and says what to use instead:
  Agent / Task: a `model` that contradicts the role's policy model (implementer, scout: exactly
    claude-sonnet-5-5; analyst: opus / claude-opus-5-5; Explore: haiku or claude-haiku-*).
    Built-in helper types (Explore, Plan, general-purpose, fork, claude-code-guide,
    statusline-setup), the repo's own agents (.claude/agents/*.md), plugin agents and a call
    with no type at all are all allowed.
  Workflow: every agent(...) call whose options the scan can read. Comments and string
    contents are masked first. Blocked only when CERTAIN: a quoted agentType outside
    `workflow_agent_types`, or a quoted model that contradicts the role. Anything the scan
    cannot be sure of (agentType missing or not a quoted literal, model not a literal,
    spread options, a workflow called only by name) is allowed with a visible warning.
"""
from __future__ import annotations

import json
import os
import re
import sys

BUILTIN_TYPES = {"Explore", "Plan", "general-purpose", "fork", "claude-code-guide", "statusline-setup"}
AGENT_CALL_RE = re.compile(r"(?<![A-Za-z0-9_$.])agent\s*\(")
AGENT_TYPE_KEY_RE = re.compile(r"(?<![A-Za-z0-9_$.])agentType\s*:\s*")
MODEL_KEY_RE = re.compile(r"(?<![A-Za-z0-9_$.])model\s*:\s*")
HINTS = {
    "implementer": "use model 'claude-sonnet-5-5' or leave `model` out (the agent file pins it)",
    "scout": "use model 'claude-sonnet-5-5' or leave `model` out (the agent file pins it)",
    "analyst": "use model 'claude-opus-5-5' or leave `model` out",
    "Explore": "use model 'haiku' or leave `model` out",
}
warnings: list[str] = []


def deny(reason: str) -> None:
    print(f"BLOCKED by guard_models.py: {reason}", file=sys.stderr)
    sys.exit(2)


def project_root() -> str:
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def load_policy() -> dict | None:
    path = os.path.join(project_root(), ".claude", "models.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            policy = json.load(fh)
    except (OSError, ValueError):
        warnings.append(f"{path} cannot be read as JSON, so the model guard is skipped; fix or delete it.")
        return None
    if not isinstance(policy, dict) or not isinstance(policy.get("roles"), dict):
        warnings.append(f"{path} has no 'roles' map, so the model guard is skipped.")
        return None
    return policy


def model_ok(policy: dict, agent_type: str, model: str) -> bool:
    role = policy["roles"].get(agent_type) or {}
    if agent_type == "Explore":
        return model == "haiku" or model.startswith("claude-haiku-")
    return model == role.get("model") or model in role.get("aliases", [])


def contradiction(policy: dict, agent_type: str, model: str) -> str | None:
    if agent_type not in policy["roles"] or model_ok(policy, agent_type, model):
        return None
    want = policy["roles"][agent_type].get("model")
    return (f"model {model!r} contradicts the policy for '{agent_type}' (must be {want}); "
            f"{HINTS.get(agent_type, 'leave `model` out')}.")


def check_agent_call(policy: dict, tool_input: dict) -> None:
    sub = tool_input.get("subagent_type")
    model = tool_input.get("model")
    if sub and model:
        why = contradiction(policy, str(sub), str(model))
        if why:
            deny(why)


def mask(text: str) -> tuple[str, str]:
    """Return (nocomments, masked). Both keep the original length and offsets.
    nocomments: comments blanked, strings intact. masked: also string contents replaced by '_'."""
    n, i = len(text), 0
    nc, mk = list(text), list(text)
    prev = ""  # last significant character, to tell a regex literal from a division
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] in "/*":
            end = text.find("\n", i) if text[i + 1] == "/" else text.find("*/", i + 2)
            end = n if end == -1 else (end if text[i + 1] == "/" else end + 2)
            for j in range(i, end):
                if text[j] != "\n":
                    nc[j] = mk[j] = " "
            i = end
            continue
        if c == "/" and (prev == "" or prev in "(,=:[!&|?{};+-*%<>~^"):
            j = i + 1
            while j < n and text[j] not in "/\n":
                j += 2 if text[j] == "\\" else 1
            if j < n and text[j] == "/":
                for k in range(i + 1, j):
                    mk[k] = "_"
                i, prev = j + 1, ")"
                continue
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                if text[k] != "\n":
                    mk[k] = "_"
            i, prev = j + 1, ")"
            continue
        if not c.isspace():
            prev = c
        i += 1
    return "".join(nc), "".join(mk)


def matching_paren(masked: str, open_idx: int) -> int | None:
    depth = 0
    for i in range(open_idx, len(masked)):
        if masked[i] == "(":
            depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def literal_at(nocomments: str, idx: int) -> tuple[str | None, bool]:
    """Value starting at idx: (string, True) for a plain quoted literal, else (None, False)."""
    if idx < len(nocomments) and nocomments[idx] in "'\"`":
        q = nocomments[idx]
        end = nocomments.find(q, idx + 1)
        if end != -1 and "${" not in nocomments[idx + 1:end]:
            return nocomments[idx + 1:end], True
    return None, False


def top_level_key(masked: str, nocomments: str, key_re: re.Pattern, lo: int, hi: int):
    """Find `key:` at nesting depth 1 inside an options object between lo and hi. Returns match end or None."""
    depth, spans = 0, []
    for i in range(lo, hi):
        ch = masked[i]
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        spans.append(depth)
    for m in key_re.finditer(masked, lo, hi):
        if spans[m.start() - lo] <= 2:
            return m.end()
    return None


def scan_workflow(policy: dict, script: str) -> list[str]:
    nocomments, masked = mask(script)
    wf_types = policy.get("workflow_agent_types") or ["implementer", "scout"]
    problems = []
    for m in AGENT_CALL_RE.finditer(masked):
        line = script.count("\n", 0, m.start()) + 1
        open_idx = m.end() - 1
        close = matching_paren(masked, open_idx)
        if close is None:
            warnings.append(f"line {line}: could not find the end of this agent(...) call; not checked.")
            continue
        snippet = " ".join(nocomments[m.start():close + 1].split())[:60]
        if "..." in masked[open_idx:close]:
            warnings.append(f"line {line}: agent(...) uses spread options ({snippet!r}); not checked.")
            continue
        type_end = top_level_key(masked, nocomments, AGENT_TYPE_KEY_RE, open_idx, close)
        if type_end is None:
            warnings.append(f"line {line}: no agentType found in agent(...) ({snippet!r}); write "
                            "{agentType: \"scout\"} inline as a quoted literal so it can be checked.")
            continue
        agent_type, is_lit = literal_at(nocomments, type_end)
        if not is_lit:
            warnings.append(f"line {line}: agentType is not a quoted literal ({snippet!r}); not checked.")
            continue
        if agent_type not in wf_types:
            problems.append(f"line {line}: agentType {agent_type!r} is not allowed in a Workflow "
                            f"(use {' or '.join(repr(t) for t in wf_types)}; for a plan use the analyst agent outside the Workflow)")
            continue
        model_end = top_level_key(masked, nocomments, MODEL_KEY_RE, open_idx, close)
        if model_end is None:
            continue
        model, lit = literal_at(nocomments, model_end)
        if not lit:
            warnings.append(f"line {line}: model is not a quoted literal ({snippet!r}); not checked.")
            continue
        why = contradiction(policy, agent_type, model)
        if why:
            problems.append(f"line {line}: {why}")
    return problems


def check_workflow_call(policy: dict, tool_input: dict) -> None:
    script = tool_input.get("script")
    path = tool_input.get("scriptPath")
    if not script and path:
        try:
            with open(path, encoding="utf-8") as fh:
                script = fh.read()
        except OSError as exc:
            warnings.append(f"could not read scriptPath {path!r} ({exc.__class__.__name__}); its agents were not checked.")
            return
    if not script:
        if tool_input.get("name"):
            warnings.append(f"workflow {tool_input['name']!r} is called by name; its agent() calls were not checked.")
        return
    problems = scan_workflow(policy, script)
    if problems:
        deny("; ".join(problems[:5]))


def finish() -> None:
    """Exit 0, making any warnings visible to the owner and to Claude."""
    if warnings:
        text = "model guard: " + " ".join(warnings)
        print(json.dumps({"systemMessage": text,
                          "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}))
    sys.exit(0)


def main() -> None:
    policy = load_policy()
    if policy is None:
        finish()
    try:
        data = json.load(sys.stdin)
    except ValueError:
        finish()
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    if tool in ("Agent", "Task"):
        check_agent_call(policy, tool_input)
    elif tool == "Workflow":
        check_workflow_call(policy, tool_input)
    finish()


if __name__ == "__main__":
    main()
