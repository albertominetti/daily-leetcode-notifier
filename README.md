# Daily LeetCode Notifier

Small Python script that:

1. Fetches **today’s LeetCode daily coding challenge**
2. Checks whether **your account** has completed **today’s** daily (an old AC on the same problem does not count)
3. Optionally sends a **Telegram** notification

Built with [`uv`](https://github.com/astral-sh/uv). **No third-party runtime dependencies** — only the Python standard library (3.10+).

> **Security:** Never commit `.env`. `LEETCODE_USERNAME` is public anyway; treat `TELEGRAM_BOT_TOKEN` like a password.

---

## Features

- Daily problem info (title, difficulty, link, optional tags)
- Public completion check (Accepted submission dated today; no login)
- Telegram messages with HTML formatting (done / not done / lookup errors)
- `--notify` sends Telegram when the daily is **incomplete** (errors always)
- `--always` also notifies when the daily is already solved
- `--silent` for quiet delivery (no sound); independent of completion
- Lookup and API failures **always alert** (never silent)
- Zero pip packages; works offline once `uv` has a Python interpreter
- **GitHub Actions** schedule with secrets stored in the repository

---

## Requirements

- [uv](https://docs.astral.sh/uv/) (or any Python 3.10+)
- Your public LeetCode username (to check *your* progress — no login needed)
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
| `LEETCODE_USERNAME` | Yes | Your public LeetCode username |
| `TELEGRAM_BOT_TOKEN` | For `--notify` | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | For `--notify` | Your user or group chat id |

**Local:** put them in `.env` (gitignored). No password, no session cookie — everything is queried from public endpoints, consuming zero session tokens.

**Telegram:** create a bot with BotFather, send `/start` to the bot, then resolve your chat id (e.g. [@userinfobot](https://t.me/userinfobot) or `getUpdates`).

---

## GitHub Actions

Workflow file: [`.github/workflows/daily-check.yml`](.github/workflows/daily-check.yml)

Runs the same check on GitHub-hosted runners and sends Telegram messages using **repository secrets** (never commit tokens to the repo).

> **Zero session limit impact:** queries use public LeetCode GraphQL endpoints, so GitHub Actions, server cron, and all your devices coexist with zero risk of logging each other out.

### 1. Add repository secrets

In your GitHub repo:

**Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|-------------|--------|
| `LEETCODE_USERNAME` | Your public LeetCode username |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat id |

### 2. Enable Actions

Push the workflow (or enable Actions if this is a fork). Scheduled workflows only run on the **default branch** (usually `main`).

### 3. Schedule (Europe/Zurich, 4 runs)

GitHub Actions `cron` is **always UTC**. The workflow uses **four** UTC times that match **Europe/Zurich in winter (CET, UTC+1)**. In summer (CEST) they run one hour later locally.

| Europe/Zurich (CET) | UTC cron | Flags | Intent |
|---------------------|----------|-------|--------|
| **10:00** | `0 9 * * *` | `--notify --silent` | Quiet if still open |
| **14:00** | `0 13 * * *` | `--notify --silent` | Quiet if still open |
| **18:00** | `0 17 * * *` | `--notify --silent` | Quiet if still open |
| **23:00** | `0 22 * * *` | `--notify` | Sound if still open |

> **Note:** GitHub can delay scheduled jobs by a few minutes. For exact local time on your machine, use system cron.

### 4. Manual run

**Actions → Daily LeetCode check → Run workflow**

Inputs:

- **notify** — send Telegram (default on; incomplete only unless **always**)
- **silent** — quiet delivery (default **off**)
- **always** — also notify when already solved (default **off**)

### 5. Job success vs “not done”

The workflow **fails only on hard errors** (exit code `2`: network / GraphQL / Telegram).  
Exit codes `0` (done), `1` (not done), and `3` (username not set) still finish the job green so Actions noise stays low; Telegram still carries the real status.

### Example: wire secrets in the workflow

Secrets are injected as environment variables (already done in the workflow):

```yaml
env:
  LEETCODE_USERNAME: ${{ secrets.LEETCODE_USERNAME }}
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

# Telegram — only if incomplete (with sound)
uv run check_daily.py --notify

# Telegram — only if incomplete (quiet)
uv run check_daily.py --notify --silent

# Telegram — also when already done
uv run check_daily.py --notify --always
uv run check_daily.py --notify --silent --always
```

Or with system Python (no deps to install):

```bash
python3 check_daily.py --env-file .env
```

### CLI flags

| Flag | Meaning |
|------|---------|
| `--username VALUE` | Override `LEETCODE_USERNAME` |
| `--json` | Machine-readable JSON |
| `--tags` | Include topic tags (hidden by default) |
| `--notify` | Send Telegram when incomplete (user/API errors always) |
| `--silent` | With `--notify`: quiet delivery (`disable_notification`). Errors always alert |
| `--always` | With `--notify`: also send when the daily is already solved |
| `--env-file PATH` | Env file to load (default: `.env`) |
| `--quiet-ok` | Suppress stdout when the daily is already done |

### Notification rules

| Situation | `--notify` | `--notify --silent` | `--notify --always` |
|-----------|------------|---------------------|---------------------|
| Daily **done** | *No message* | *No message* | Message (+ sound unless `--silent`) |
| Daily **not done** | Message + sound | Message, quiet | Same |
| **Can't verify** (20+ ACs today) | Message + sound | Message, quiet | Same |
| User lookup error | **Alert** | **Alert** | **Alert** |
| API / network error | **Alert** | **Alert** | **Alert** |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Daily is **done** |
| `1` | Daily is **not done** (or can't verify) |
| `2` | LeetCode / Telegram / network error |
| `3` | Username missing (cannot evaluate completion) |

---

## Scheduling with cron (local examples)

The script does **not** hardcode times. You choose when it runs via **cron**, **GitHub Actions** (see above), or systemd timers. Below is one sensible daily pattern for a machine crontab (local timezone).

### Suggested logic

| Local time | Flags | Intent |
|------------|-------|--------|
| **10:00** | `--notify --silent` | Quiet if still open |
| **14:00** | `--notify --silent` | Quiet if still open |
| **18:00** | `--notify --silent` | Quiet if still open |
| **23:00** | `--notify` | Sound if still open |

**Why this combo for cron?**

- `--notify` already skips when done; no extra flag needed on schedules.
- Daytime uses `--silent`; night does not (sound if still open).
- Use `--always` only when you want a “DONE” confirmation.
- API / lookup errors always alert with sound.

```text
        10:00          14:00          18:00          23:00
          |              |              |              |
          v              v              v              v
     quiet if open  quiet if open  quiet if open  alert if open
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

Only evenings, quiet then loud (skip if done):

```cron
0 19 * * * cd PROJECT && UV run check_daily.py --notify --silent
0 22 * * * cd PROJECT && UV run check_daily.py --notify
```

Always report status every 4 hours (including when done):

```cron
0 */4 * * * cd PROJECT && UV run check_daily.py --notify --silent --always
```

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
├── tests/
│   └── test_check_daily.py        # completion and status-copy unit tests
└── .github/workflows/
    └── daily-check.yml            # scheduled GitHub Action
```

---

## How completion is detected

Against LeetCode’s public GraphQL API (`https://leetcode.com/graphql`):

1. Fetches today's challenge from `activeDailyCodingChallengeQuestion` (its problem slug and UTC date)
2. Fetches the user's recent submissions from `recentSubmissionList` and `recentAcSubmissionList`
3. Daily is **done** only when an **Accepted** submission on today's problem exists with a timestamp on **today's UTC date**
4. If no accepted submission occurred today, status is **NOT DONE**
5. If 20+ accepted submissions occurred today and the daily wasn't among them, status is **CAN'T VERIFY ⚠️** (the public history buffer overflowed)

This tool **only reads** public status. It does not submit solutions and requires no login or session cookies.

---

## License

[MIT](LICENSE)
