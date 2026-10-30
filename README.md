# Server Bot

A Telegram bot that monitors and manages two self-hosted NixOS servers running Dockerized
business apps behind Cloudflare Tunnels.

The same code runs on both servers; only the `.env` differs. Each bot also
checks the websites served by the *other* server, so if one machine dies
completely, the surviving one reports it.

## Features

**Health checks** (`/status`)
- Websites: HTTP status and latency, with plain-language explanations of
  Cloudflare errors (502 bad gateway, 530 tunnel down, ...)
- Docker containers: required containers are running
- Cloudflare Tunnels: exactly one connector per tunnel. Catches the
  "rogue connector" failure where a second machine joins a tunnel and
  requests randomly fail
- Disk usage

**Background monitor**
- Runs every check on an interval and notifies only on state changes
  (problem detected / recovered)
- Flap protection: a new state must be seen N times in a row before
  alerting, so a service bouncing up and down doesn't spam the chat

**Remote actions** (`/restart <app>`, `/restart_tunnel`, `/reboot`)
- Every action requires an inline-button confirmation that expires after 60s
- One action at a time (async lock); every action is logged with who ran it
- Available actions are configured per server
- No shutdown command by design: nobody could power the server back on remotely

**Security**
- Chat ID allowlist; unknown users get no reply at all
- The bot uses `sudo` for exactly three whitelisted commands
  (restart its tunnel, reboot, restart itself), configured in NixOS

## Architecture

```
bot/
├── main.py      # entry point, /start and /status, error handler, command menu
├── config.py    # all settings from .env
├── auth.py      # @only_allowed decorator (chat ID allowlist)
├── checks.py    # health checks, each returns a CheckResult(name, ok, detail)
├── monitor.py   # periodic job, state tracking, flap protection, notifications
└── actions.py   # restart / reboot with confirmation buttons
```

- Fully async (`python-telegram-bot`, `httpx`, `asyncio` subprocesses),
  so a slow check never blocks the bot
- Checks run concurrently with `asyncio.gather`
- `update_states()` in `monitor.py` is a pure function, easy to unit test

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in BOT_TOKEN and ALLOWED_CHAT_IDS
python -m bot.main
```

Send `/start` to the bot. While your chat ID is not allowed yet, the log
shows `Access denied: chat_id=...`; put that number in `ALLOWED_CHAT_IDS`.

### Configuration (`.env`)

| Variable | Example | Meaning |
|---|---|---|
| `BOT_TOKEN` | | Token from @BotFather (one bot per server) |
| `ALLOWED_CHAT_IDS` | `12345` | Comma-separated chat IDs allowed to use the bot |
| `SERVER_NAME` | `Cikarang` | Shown in every message |
| `CONTAINERS` | `sge-backend,sge-caddy` | Containers that must be running |
| `TUNNEL_IDS` | `96a2...,a014...` | Tunnels whose connector count is checked (needs `cloudflared` login) |
| `DISK_PATH` / `DISK_LIMIT_PERCENT` | `/` / `85` | Disk check |
| `MONITOR_INTERVAL` / `CONFIRM_AFTER` | `60` / `2` | Monitor interval (s) and flap protection |
| `APPS` | `sge,espe` | Apps for `/restart <app>` (restarts containers named `<app>-*`) |
| `TUNNEL_SERVICE` | `cloudflared-tunnel-<id>` | systemd unit for `/restart_tunnel` |
| `ALLOW_REBOOT` | `true` | Enables `/reboot`. Keep `false` on a laptop |

## Deployment

The bot runs as a NixOS systemd service (`server-bot`) under `/srv/apps/server-bot`.

```bash
./deploy-bot.sh                 # both servers
./deploy-bot.sh sge             # one server
```

The script rsyncs the code (never `.env` or `venv`), installs requirements
and restarts the service.
