# claude-herdr-dashboard

Companion-таб для Claude Code в [herdr](https://herdr.dev): статус агента, расход контекста,
лог tool calls и дерево файлов с подсветкой изменённых в сессии.

- `dash.py` — Textual TUI (`uv run --script`, зависимости ставятся сами). `--pane <id>`, `--window <tokens>` (по умолчанию 1M).
- `herdr-dash [--hook] [pane-id]` — открывает таб `<имя таба агента>-dash` рядом с агентом; повторно не дублирует.
  Dashboard закрывается сам, когда агент уходит из pane.

Автозапуск — SessionStart hook в `~/.claude/settings.json`:

```json
{ "matcher": "^(startup|resume)$",
  "hooks": [{ "type": "command", "command": "/path/to/herdr-dash --hook", "timeout": 15 }] }
```
