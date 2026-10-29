Library:
- `python-telegram-bot` (version 21+): very popular library bot Telegram,
  complete docs. Use `[job-queue]` for periodic assignments (checking every minutes)
- `httpx`: checking website (detect bad gateway)
- `python-dotenv`: read token and settings from file `.env`

Project Structure:
server-bot/
  bot/
    main.py #entry point, list of command
    config.py #read .env (token, chat ID, server name, etc)
    auth.oy #checking chat ID can be use or not
    checks.py #check website, disk, container, other server
    actions.py #restart sge/espe, restart tunnel, reboot
    monitor.py #routine check + send notif if status changes
  .env.example
  .gitignore #required to put .env and venv/
  requirements.txt
  README.md
