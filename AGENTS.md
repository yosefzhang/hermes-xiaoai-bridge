# AGENTS.md

Repo-specific guidance for OpenCode sessions working on `hermes-xiaoai-bridge`.

## What this repo is

A thin Python CLI that bridges a Xiaomi 小爱 speaker with the Hermes Agent. Scripts handle **only** Xiaomi network I/O; **all intent/reasoning logic lives in Hermes**. Bottom layer is [mijiaAPI](https://github.com/yosefzhang/mijia-api).

- `SKILL.md` — Hermes skill definition (loaded by Hermes, not executed here).
- `script/monitor.py` — Flow A polling daemon (self-contained, no external project deps beyond mijiaAPI and requests).
All speaker interaction commands (`run`, `play-text`, `conversations`, `login`) are handled directly by `mijiaAPI` CLI (v3.3.0+). No wrapper scripts needed.

## Commands

```bash
# Setup (venv already exists at .venv)
source .venv/bin/activate && pip install -r requirements.txt

# Login via mijiaAPI CLI (two-step: generate → poll, must run in immediate succession)
mijiaAPI login -g -p ~/.config/hermes-xiaoai-bridge/auth.json
mijiaAPI login --poll -p ~/.config/hermes-xiaoai-bridge/auth.json

# Combined (blocking, for local use):
mijiaAPI login -p ~/.config/hermes-xiaoai-bridge/auth.json           # refresh token or QR login
mijiaAPI login -p ~/.config/hermes-xiaoai-bridge/auth.json -f        # force re-scan

# List devices
mijiaAPI -l -p ~/.config/hermes-xiaoai-bridge/auth.json

# Send command to speaker
mijiaAPI run "<指令>" --wifispeaker_name "<米家名称>" -p ~/.config/hermes-xiaoai-bridge/auth.json

# TTS only
mijiaAPI play-text "<文本>" --wifispeaker_name "<米家名称>" -p ~/.config/hermes-xiaoai-bridge/auth.json

# Get conversations
mijiaAPI conversations --speaker_name "<米家名称>" --limit 20 -p ~/.config/hermes-xiaoai-bridge/auth.json

# Monitor daemon (background with nohup)
.venv/bin/python script/monitor.py --speaker all --interval 1
```

## Conventions that are easy to get wrong

- **stdout is machine-readable JSON; logs go to stderr.** Preserve this — Hermes parses stdout. Never `print()` debug info to stdout.
- **mijiaAPI `run` and `play-text` are different operations.** `play-text` uses MIoT `play-text` (siid=7, aiid=3); `run` uses `execute-text-directive` (siid=7, aiid=4).
- **Conversations are returned newest-first.** Dedup uses `requestId`.
- **Logs go to stderr and `{workspace}/monitor.log`** via `setup_logging()`.
- **Log level** defaults to INFO. Override with `--verbose` (DEBUG) or config `log_level`.
- **`run` is async** — success of the HTTP call does not mean the speaker executed it. Confirm via a follow-up `conversations` poll.
- **Login: generate then poll immediately.** Don't download the QR image (causes ticket expiry). Send the link only, then start poll RIGHT AFTER. Ticket TTL is ~120s.
- **QR URL only, never download QR image.** Downloading may consume the ticket.
- Speaker names in CLI use **米家 APP 中的设备名称** (e.g. "卧室小爱"), not config keys.

## Files that must never be committed

`auth.json`, `config.json`, `monitor_state.json`, `monitor.lock`, `agent.md` — all gitignored.

## Runtime paths

All under `workspace` (default `~/.config/hermes-xiaoai-bridge`):

- `auth.json` — credentials from `mijiaAPI login`.
- `config.json` — runtime config (workspace, default_wifispeaker, monitor)
- `monitor_state.json` — Flow A polling cursor.
- `monitor.lock` — Flock-based singleton guard.
- `monitor.log` — log file.

## Config

`config.json` fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `workspace` | path | — | Base directory for auth, state, logs |
| `log_level` | string | INFO | DEBUG/INFO/WARNING/ERROR |
| `default_wifispeaker` | string | — | Default speaker name for non-monitor commands |
| `monitor.enabled` | bool | true | Enable/disable the polling daemon |
| `monitor.wifispeakers` | list[string] | — | Speaker names to monitor |
| `monitor.poll_interval` | int | 1 | Monitor polling interval in seconds |
| `monitor.webhook` | URL | — | Webhook endpoint monitor.py POSTs to |

## Error → user action mapping

| Error | Fix |
|---|---|
| `auth.json not found` / `missing passToken` | Log in: `mijiaAPI login -p ~/.config/hermes-xiaoai-bridge/auth.json` |
| `conversation API 401` / `micoapi login failed` | token expired → `mijiaAPI login -f -p ~/.config/hermes-xiaoai-bridge/auth.json` |
| `xiaomiio login failed` | token expired → `mijiaAPI login -f -p ~/.config/hermes-xiaoai-bridge/auth.json` |
| `config file not found` | Create `~/.config/hermes-xiaoai-bridge/config.json` (see SKILL.md for format) |
