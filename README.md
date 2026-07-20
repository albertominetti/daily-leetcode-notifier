# Daily LeetCode Notifier

Small Python script that:

1. Fetches **today’s LeetCode daily coding challenge**
2. Checks whether **your account** has already solved it
3. Optionally sends a **Telegram** notification

Built with [`uv`](https://github.com/astral-sh/uv). **No third-party runtime dependencies** — only the Python standard library (3.10+).

> **Security:** Never commit `.env`. Treat `LEETCODE_SESSION` and `TELEGRAM_BOT_TOKEN` like passwords.

---

## Features

- Daily problem info (title, difficulty, link, optional tags)
- Completion check for the authenticated user
- Telegram messages with HTML formatting (done / not done / session errors)
- `--silent` for quiet Telegram deliveries (no sound/vibration)
- Auth and API failures **always alert** (never silent)
- Zero pip packages; works offline once `uv` has a Python interpreter
- **GitHub Actions** schedule with secrets stored in the repository

---

## Requirements

- [uv](https://docs.astral.sh/uv/) (or any Python 3.10+)
- LeetCode account + `LEETCODE_SESSION` cookie (to check *your* progress)
- Telegram bot token + chat id (only if you use `--notify`)

---

## Setup

### Local

```bash
git clone <your-repo-url> daily-leetcode-notifier
cd daily-leetcode-notifier

uv sync
cp .env.example .env
# edit .env — see below
```

### Environment variables / secrets

| Variable | Required | Purpose |
|----------|----------|---------|
| `LEETCODE_SESSION` | Yes (for status) | Browser session cookie from leetcode.com |
| `LEETCODE_CSRFTOKEN` | No | Optional CSRF cookie |
| `TELEGRAM_BOT_TOKEN` | For `--notify` | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | For `--notify` | Your user or group chat id |

**Local:** put them in `.env` (gitignored).

**LeetCode cookie:** log in at [leetcode.com](https://leetcode.com) → DevTools → Application/Storage → Cookies → copy `LEETCODE_SESSION`.

**Telegram:** create a bot with BotFather, send `/start` to the bot, then resolve your chat id (e.g. [@userinfobot](https://t.me/userinfobot) or `getUpdates`).

---

## GitHub Actions

Workflow file: [`.github/workflows/daily-check.yml`](.github/workflows/daily-check.yml)

Runs the same check on GitHub-hosted runners and sends Telegram messages using **repository secrets** (never commit tokens to the repo).

### 1. Add repository secrets

In your GitHub repo:

**Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|-------------|--------|
| `LEETCODE_SESSION` | Your LeetCode session cookie |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat id |
| `LEETCODE_CSRFTOKEN` | *(optional)* CSRF cookie |

### 2. Enable Actions

Push the workflow (or enable Actions if this is a fork). Scheduled workflows only run on the **default branch** (usually `main`).

### 3. Schedule (Europe/Zurich)

GitHub Actions `cron` is **always UTC**, so the workflow cannot set `CRON_TZ` directly. Instead it:

1. Fires at every **UTC** hour that can be 10:00 / 14:00 / 18:00 / 23:00 in **Europe/Zurich** under both CET (UTC+1) and CEST (UTC+2)
2. Reads the current hour with `TZ=Europe/Zurich`
3. **Runs** only on those local hours; **skips** the off-season UTC fire

| Europe/Zurich | Flags | Intent |
|---------------|-------|--------|
| **10:00** | `--notify --silent` | Quiet status |
| **14:00** | `--notify --silent` | Quiet status |
| **18:00** | `--notify --silent` | Quiet status |
| **23:00** | `--notify` | Alert with sound if still not done |

| Season | Zurich target | UTC cron fires |
|--------|---------------|----------------|
| CET (winter, UTC+1) | 10 / 14 / 18 / 23 | 09 / 13 / 17 / 22 |
| CEST (summer, UTC+2) | 10 / 14 / 18 / 23 | 08 / 12 / 16 / 21 |

> **Note:** GitHub can delay scheduled jobs by several minutes under load. For a hard local-time guarantee on your machine, keep a system crontab as a backup.

### 4. Manual run

**Actions → Daily LeetCode check → Run workflow**

Inputs:

- **notify** — send Telegram (default on)
- **silent** — quiet delivery (default on)

### 5. Job success vs “not done”

The workflow **fails only on hard errors** (exit code `2`: network / GraphQL / Telegram).  
Exit codes `0` (done), `1` (not done), and `3` (not signed in) still finish the job green so Actions noise stays low; Telegram still carries the real status.

### Example: wire secrets in the workflow

Secrets are injected as environment variables (already done in the workflow):

```yaml
env:
  LEETCODE_SESSION: ${{ secrets.LEETCODE_SESSION }}
  LEETCODE_CSRFTOKEN: ${{ secrets.LEETCODE_CSRFTOKEN }}
  TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
  TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

---

## Usage

```bash
# Print today’s daily + your completion status
uv run check_daily.py

# Same, with topic tags
uv run check_daily.py --tags

# JSON (for scripts)
uv run check_daily.py --json

# Telegram — quiet status (done or not done)
uv run check_daily.py --notify --silent

# Telegram — alert with sound only if still not done
uv run check_daily.py --notify
```

Or with system Python (no deps to install):

```bash
python3 check_daily.py --env-file .env
```

### CLI flags

| Flag | Meaning |
|------|---------|
| `--session VALUE` | Override `LEETCODE_SESSION` |
| `--csrf VALUE` | Override CSRF cookie |
| `--json` | Machine-readable JSON |
| `--tags` | Include topic tags (hidden by default) |
| `--notify` | Send a Telegram message |
| `--silent` | With `--notify`: quiet delivery (`disable_notification`). Auth/API errors ignore this and always alert |
| `--env-file PATH` | Env file to load (default: `.env`) |
| `--quiet-ok` | Suppress stdout when the daily is already done |

### Notification rules

| Situation | `--notify --silent` | `--notify` |
|-----------|---------------------|------------|
| Daily **done** | Silent “DONE” | *No message* |
| Daily **not done** | Silent “NOT DONE” | **Alert** (sound) |
| Session invalid | **Alert** (never silent) | **Alert** |
| API / network error | **Alert** (never silent) | **Alert** |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Signed in and daily is **done** |
| `1` | Signed in and daily is **not done** |
| `2` | LeetCode / Telegram / network error |
| `3` | Not signed in (cannot evaluate completion) |

---

## Scheduling with cron (local examples)

The script does **not** hardcode times. You choose when it runs via **cron**, **GitHub Actions** (see above), or systemd timers. Below is one sensible daily pattern for a machine crontab (local timezone).

### Suggested logic

| Local time | Flags | Intent |
|------------|-------|--------|
| **10:00** | `--notify --silent` | Morning check-in: quiet status whether done or not |
| **14:00** | `--notify --silent` | Midday reminder: still quiet |
| **18:00** | `--notify --silent` | Evening check: still quiet |
| **23:00** | `--notify` | Last call: **only if not done**, with sound so you notice |

**Why silent earlier and loud at night?**

- During the day you want awareness without interruption.
- Late evening you want a real alert if the streak/daily is still open.
- Session/cookie problems should always wake you up, so the script **never** sends auth or API failures as silent messages (even with `--silent`).

```text
        10:00          14:00          18:00          23:00
          |              |              |              |
          v              v              v              v
     quiet status   quiet status   quiet status   alert if open
     (done/not)     (done/not)     (done/not)     (sound on)
```

### Example crontab

Replace `PROJECT` with your clone path and `UV` with `which uv` (or use a full path to `python3`).

```cron
# Daily LeetCode notifier (machine local timezone)
# Quiet status through the day
0 10 * * * cd PROJECT && UV run check_daily.py --notify --silent
0 14 * * * cd PROJECT && UV run check_daily.py --notify --silent
0 18 * * * cd PROJECT && UV run check_daily.py --notify --silent

# Night: sound only if the daily is still incomplete
0 23 * * * cd PROJECT && UV run check_daily.py --notify
```

Concrete example:

```cron
0 10 * * * cd /home/you/daily-leetcode-notifier && /home/you/.local/bin/uv run check_daily.py --notify --silent
0 14 * * * cd /home/you/daily-leetcode-notifier && /home/you/.local/bin/uv run check_daily.py --notify --silent
0 18 * * * cd /home/you/daily-leetcode-notifier && /home/you/.local/bin/uv run check_daily.py --notify --silent
0 23 * * * cd /home/you/daily-leetcode-notifier && /home/you/.local/bin/uv run check_daily.py --notify
```

Install:

```bash
crontab -e
# paste your lines, save
crontab -l   # verify
```

**Timezone:** cron uses the system local timezone unless you set `CRON_TZ` (where supported).

**Environment:** cron has a minimal `PATH`. Prefer absolute paths to `uv`/`python3`, and keep secrets in the project `.env` (the script loads it automatically).

**Optional logging** (not required):

```cron
0 10 * * * cd PROJECT && UV run check_daily.py --notify --silent >>/tmp/leetcode-daily.log 2>&1
```

### Other schedules (examples)

Only evenings, quiet then loud:

```cron
0 19 * * * cd PROJECT && UV run check_daily.py --notify --silent
0 22 * * * cd PROJECT && UV run check_daily.py --notify
```

Only alert when incomplete (no “already done” noise):

```cron
0 */4 * * * cd PROJECT && UV run check_daily.py --notify
```

(`--notify` without `--silent` skips Telegram when the daily is already done.)

---

## Project layout

```text
daily-leetcode-notifier/
├── check_daily.py                 # single script (CLI + LeetCode + Telegram)
├── pyproject.toml                 # uv project metadata
├── uv.lock
├── .python-version
├── .env.example                   # template — copy to .env
├── .gitignore
├── LICENSE
├── README.md
└── .github/workflows/
    └── daily-check.yml            # scheduled GitHub Action
```

---

## How completion is detected

Against LeetCode’s GraphQL API (`https://leetcode.com/graphql`):

- Daily node `userStatus == Finish`, and/or
- Problem `status == ac` for the signed-in user

This tool **only reads** status. It does not submit solutions.

Session cookies expire; when Telegram says the session is invalid, refresh `LEETCODE_SESSION` in `.env`.

---

## License

[MIT](LICENSE)
