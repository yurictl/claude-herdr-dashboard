# claude-herdr-dashboard

A companion tab for [Claude Code](https://claude.com/claude-code) running in [herdr](https://herdr.dev):
agent status, context usage, a live tool-call log, and a file tree with files edited in the session highlighted.

```
◐ working   claude-opus-5-5   tools: 18
━━━━━━━━━━╸━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ctx 163.4k / 1000k (16%)
📂 project                │── prompt: add a hook ...
├── 📄 dash.py ✎          │15:14:40 ▸ Bash       herdr tab list ...
└── 📄 herdr-dash ✎       │15:15:42 ▸ Write      /…/dash.py
                          │ Edited files:
                          │   ✎ dash.py  ×3
```

## How it works

`herdr agent get <pane>` reports the Claude Code session id of the agent in a pane. The dashboard finds the
matching transcript (`~/.claude/projects/*/<session-id>.jsonl`), tails it once a second, and renders:

- **status** — `working` / `idle` / `blocked` / `done` from herdr
- **context** — input + cache tokens of the latest assistant turn vs. the context window
- **tool calls** — time, tool, command/path; failed calls are shown in red; your prompts as separators
- **file tree** — the agent's cwd; files touched by `Edit`/`Write` are marked `✎`

Nothing is injected into Claude Code itself — no plugin or mod required.

## Files

- `dash.py` — the Textual TUI. Runs via `uv run --script`, dependencies are installed automatically.
  Options: `--pane <id>`, `--window <tokens>` (default 1M).
- `herdr-dash [--hook] [pane-id]` — opens a sibling tab named `<agent tab>-dash` next to the agent
  (defaults to the calling pane) and won't open a duplicate. The dashboard exits — closing its tab — once the
  agent leaves the pane.

Keys: `r` reload tree, `c` clear log, `q` quit.

## Requirements

herdr, [uv](https://docs.astral.sh/uv/), jq.

## Auto-open on session start

Add a SessionStart hook to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "^(startup|resume)$",
        "hooks": [{ "type": "command", "command": "/path/to/herdr-dash --hook", "timeout": 15 }]
      }
    ]
  }
}
```

`--hook` is silent and does nothing outside herdr or when the dash tab already exists.
