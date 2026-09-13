# TeraDrop — fast TeraBox delivery for Telegram

TeraDrop accepts a public share link, resolves the available file options, and
lets a user stream, open a direct link, or send the file into the chat. User
messages use compact formatting, inline actions, and live download/upload
progress.

## What changed

- **Large uploads:** Docker Compose now includes Telegram's local Bot API
  server. It removes the hosted Bot API's ~50 MB ceiling and supports files up
  to 2,000 MB, subject to disk space, bandwidth, and the configured limit.
- **Real upload progress:** files are sent as a streamed multipart request;
  the whole file is not read into memory.
- **Safer concurrency:** non-blocking update handlers, a bounded download
  semaphore, a larger Telegram connection pool, unique temporary filenames,
  and SQLite WAL mode make simultaneous users much more reliable.
- **Owner dashboard:** `/owner` opens an inline admin panel with stats, users,
  logs, settings, maintenance mode, and refresh controls.
- **Cleaner user experience:** compact headings, blockquotes, clearer errors,
  expired button protection, and per-user cached file actions.

## Docker setup

1. Create the environment file:

   ```bash
   cp .env.example .env
   ```

2. Add:

   - `BOT_TOKEN` from BotFather
   - `OWNER_IDS` with your numeric Telegram user id
   - `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from `my.telegram.org`

3. Start both services:

   ```bash
   docker compose up -d --build
   ```

The local Bot API service is private to the Docker network. The bot itself is
available on port `8080` for health checks.

## Important upload limitation

The local Bot API server is required for files above the hosted Bot API limit.
A normal Telegram user account's 2 GB upload allowance does not automatically
apply to bots. If `BOT_API_URL` is empty, TeraDrop deliberately keeps the
send-to-chat option at 49 MB and still exposes Stream and Direct.

`MAX_FILE_MB` can be set from `1` to `2000`. The owner can also use:

```text
/setlimit 2000
```

The effective value is always capped by the active Telegram transport.

## Owner controls

`/owner` opens the dashboard. The command equivalents remain available:

| Command | Action |
|---|---|
| `/stats` | Usage overview |
| `/users` | Recent users |
| `/logs` | Recent errors |
| `/broadcast <text>` | Message known users |
| `/ban <user_id>` / `/unban <user_id>` | Block or restore a user |
| `/auth <user_id>` / `/unauth <user_id>` | Manage private allow-list |
| `/maintenance on\|off` | Pause public processing |
| `/setwelcome` | Set or reply with a welcome message |
| `/setcaption <template>` | Set `{filename}`, `{size}`, `{url}` caption |
| `/setlimit <mb>` | Set the maximum send size |

## Configuration

See `.env.example`. `MAX_CONCURRENT=3` is a safe starting point; increase it
only when the server has enough CPU, memory, disk throughput, and upstream
bandwidth for concurrent multi-hundred-megabyte transfers.