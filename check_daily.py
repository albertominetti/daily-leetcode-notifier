#!/usr/bin/env python3
"""
Check LeetCode's daily coding challenge and whether a user has completed
today's daily (a lifetime AC on the same problem does not count).

Fully public, zero auth: the daily problem and the user's recent
submissions are both readable without logging in, so a run consumes none
of LeetCode's ~2 parallel sessions. Any number of workers (server cron,
GitHub Actions, ...) and devices coexist. Only LEETCODE_USERNAME (the
public LeetCode username) is configured — no password, no session,
nothing stored.

Done = an Accepted submission on today's daily problem, dated today (UTC).
An old AC without one today does not count.

Optional Telegram alerts via --notify (incomplete by default; use --always
to also report when done; --silent for quiet deliveries).
Lookup/API errors always notify and are never silent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEETCODE_GRAPHQL = "https://leetcode.com/graphql"
LEETCODE_ORIGIN = "https://leetcode.com"
TELEGRAM_API = "https://api.telegram.org"

# userStatus on the daily challenge node
STATUS_NOT_START = "NotStart"
STATUS_FINISH = "Finish"

# question.status for the logged-in user
QSTATUS_AC = "ac"
QSTATUS_NOTAC = "notac"

# How many recent submissions to request from LeetCode (capped at 20 upstream).
RECENT_LIMIT = 20


DAILY_QUERY = """
query questionOfToday {
  activeDailyCodingChallengeQuestion {
    date
    userStatus
    link
    question {
      questionFrontendId
      title
      titleSlug
      difficulty
      acRate
      status
      topicTags {
        name
      }
    }
  }
}
"""

RECENT_SUBMISSIONS_QUERY = """
query recentSubmissions($username: String!, $limit: Int!) {
  matchedUser(username: $username) {
    username
  }
  recentSubmissionList(username: $username, limit: $limit) {
    titleSlug
    timestamp
    statusDisplay
  }
  recentAcSubmissionList(username: $username, limit: $limit) {
    titleSlug
    timestamp
  }
}
"""


@dataclass
class DailyChallenge:
    date: str
    title: str
    title_slug: str
    difficulty: str
    frontend_id: str
    link: str
    ac_rate: float | None
    topic_tags: list[str]
    # Synthesized from public submissions (no auth)
    daily_user_status: str | None  # NotStart | Finish | ...
    question_status: str | None  # ac | notac | None
    is_done: bool
    username: str | None
    user_found: bool
    cant_verify: bool = False  # 20+ ACs today rolled off the public window


class LeetCodeError(RuntimeError):
    """Raised when LeetCode GraphQL requests fail or return unexpected data."""


class TelegramError(RuntimeError):
    """Raised when Telegram Bot API requests fail."""


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (no override)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def graphql(
    query: str,
    *,
    variables: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """POST a GraphQL query. No auth — all data used here is public."""
    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "daily-leetcode-notifier/1.0 "
            "(+https://github.com/local/daily-leetcode-notifier)"
        ),
        "Origin": LEETCODE_ORIGIN,
        "Referer": f"{LEETCODE_ORIGIN}/problemset/",
    }

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        LEETCODE_GRAPHQL,
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LeetCodeError(
            f"HTTP {exc.code} from LeetCode GraphQL: {detail[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise LeetCodeError(f"Network error talking to LeetCode: {exc.reason}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LeetCodeError(f"Invalid JSON from LeetCode: {raw[:200]}") from exc

    if data.get("errors"):
        messages = "; ".join(
            e.get("message", str(e)) for e in data["errors"] if isinstance(e, dict)
        )
        raise LeetCodeError(f"GraphQL errors: {messages}")

    if "data" not in data:
        raise LeetCodeError(f"Unexpected GraphQL response: {raw[:200]}")

    return data["data"]


def utc_day(timestamp: str | int) -> str | None:
    """UTC calendar day (YYYY-MM-DD) of a LeetCode epoch timestamp."""
    try:
        moment = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return moment.strftime("%Y-%m-%d")


def summarize_daily_submissions(
    submissions: list[dict[str, Any]],
    *,
    slug: str,
    day: str,
) -> tuple[bool, bool]:
    """
    Verdict from public recent submissions: (done, attempted_today).

    done = Accepted submission on this problem dated today (UTC).
    """
    done = False
    attempted_today = False
    for sub in submissions:
        if not isinstance(sub, dict) or sub.get("titleSlug") != slug:
            continue
        accepted = str(sub.get("statusDisplay") or "").lower() == "accepted"
        if utc_day(sub.get("timestamp") or "") == day:
            if accepted:
                done = True
            else:
                attempted_today = True
    if done:
        attempted_today = False
    return done, attempted_today


def is_challenge_done(daily_user_status: str | None) -> bool:
    """
    Done only when the synthesized daily status is Finish, i.e. an Accepted
    submission on today's problem dated today.
    """
    return bool(
        daily_user_status and daily_user_status.lower() == STATUS_FINISH.lower()
    )


def fetch_daily_challenge(username: str) -> DailyChallenge:
    """
    Fetch today's daily plus the user's public recent submissions, and
    synthesize the completion verdict. No auth, no session.
    """
    data = graphql(DAILY_QUERY)
    node = data.get("activeDailyCodingChallengeQuestion")
    if not node:
        raise LeetCodeError("No active daily coding challenge returned.")

    question = node.get("question") or {}
    tags = [t.get("name") for t in (question.get("topicTags") or []) if t.get("name")]

    title_slug = question.get("titleSlug") or ""
    link_path = node.get("link") or f"/problems/{title_slug}/"
    if not link_path.startswith("http"):
        link = f"{LEETCODE_ORIGIN}{link_path}"
    else:
        link = link_path
    day = node.get("date") or ""

    recent = graphql(
        RECENT_SUBMISSIONS_QUERY,
        variables={"username": username, "limit": RECENT_LIMIT},
    )
    matched = recent.get("matchedUser") or {}
    resolved = matched.get("username") or None
    if not resolved:
        # Unknown user: cannot evaluate completion.
        return DailyChallenge(
            date=day,
            title=question.get("title") or "",
            title_slug=title_slug,
            difficulty=question.get("difficulty") or "Unknown",
            frontend_id=str(question.get("questionFrontendId") or ""),
            link=link,
            ac_rate=question.get("acRate"),
            topic_tags=tags,
            daily_user_status=None,
            question_status=None,
            is_done=False,
            username=None,
            user_found=False,
            cant_verify=False,
        )

    recent_ac_list = [
        s for s in (recent.get("recentAcSubmissionList") or []) if isinstance(s, dict)
    ]
    today_ac_count = sum(
        1 for s in recent_ac_list if utc_day(s.get("timestamp") or "") == day
    )

    subs: list[dict[str, Any]] = [
        s for s in (recent.get("recentSubmissionList") or []) if isinstance(s, dict)
    ]
    # Also merge recentAcSubmissionList: ensures a flurry of non-accepted
    # attempts on other problems does not push today's accepted daily out of
    # the 20-item window.
    for ac in recent_ac_list:
        subs.append({**ac, "statusDisplay": "Accepted"})

    done, attempted_today = summarize_daily_submissions(
        subs, slug=title_slug, day=day
    )

    cant_verify = False
    if done:
        daily_user_status: str | None = STATUS_FINISH
        question_status: str | None = QSTATUS_AC
    elif len(recent_ac_list) >= 20 and today_ac_count >= 20:
        # If the user has 20 or more Accepted submissions today and the daily
        # wasn't among them, older submissions from earlier today have rolled
        # off the 20-item public window: we cannot verify whether it was done.
        cant_verify = True
        daily_user_status = None
        question_status = None
    elif attempted_today:
        daily_user_status = STATUS_NOT_START
        question_status = QSTATUS_NOTAC
    else:
        daily_user_status = STATUS_NOT_START
        question_status = None

    return DailyChallenge(
        date=day,
        title=question.get("title") or "",
        title_slug=title_slug,
        difficulty=question.get("difficulty") or "Unknown",
        frontend_id=str(question.get("questionFrontendId") or ""),
        link=link,
        ac_rate=question.get("acRate"),
        topic_tags=tags,
        daily_user_status=daily_user_status,
        question_status=question_status,
        is_done=is_challenge_done(daily_user_status),
        username=resolved,
        user_found=True,
        cant_verify=cant_verify,
    )


def format_human(challenge: DailyChallenge, *, show_tags: bool = False) -> str:
    lines: list[str] = []
    lines.append("LeetCode Daily Challenge")
    lines.append("=" * 40)
    lines.append(f"Date:       {challenge.date}")
    lines.append(
        f"Problem:    #{challenge.frontend_id} {challenge.title} "
        f"({challenge.difficulty})"
    )
    lines.append(f"Link:       {challenge.link}")
    if challenge.ac_rate is not None:
        lines.append(f"Accept %:   {challenge.ac_rate:.1f}%")
    if show_tags and challenge.topic_tags:
        lines.append(f"Tags:       {', '.join(challenge.topic_tags)}")
    lines.append("-" * 40)

    if not challenge.user_found:
        lines.append("User:       (unknown user)")
        lines.append("Status:     UNKNOWN — user not found")
        lines.append("")
        lines.append(
            "Tip: LEETCODE_USERNAME must be your public LeetCode username."
        )
    else:
        who = challenge.username or "(user)"
        lines.append(f"User:       {who}")
        if challenge.is_done:
            lines.append("Status:     DONE ✓  — daily challenge already solved")
        elif challenge.cant_verify:
            lines.append(
                "Status:     CAN'T VERIFY ⚠️  — 20+ accepted submissions today; "
                "older submissions rolled off public history"
            )
        else:
            if challenge.question_status == QSTATUS_NOTAC:
                lines.append(
                    "Status:     NOT DONE  — attempted today but not accepted"
                )
            else:
                lines.append("Status:     NOT DONE  — not solved today")

    return "\n".join(lines)


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def difficulty_emoji(difficulty: str) -> str:
    mapping = {
        "Easy": "🟢",
        "Medium": "🟡",
        "Hard": "🔴",
    }
    return mapping.get(difficulty, "⚪")


def format_telegram_status(challenge: DailyChallenge) -> str:
    """Build an HTML-formatted Telegram message for the daily status."""
    date = _html_escape(challenge.date)
    title = _html_escape(challenge.title)
    difficulty = _html_escape(challenge.difficulty)
    frontend_id = _html_escape(challenge.frontend_id)
    link = _html_escape(challenge.link)
    who = _html_escape(challenge.username or "you")
    diff_icon = difficulty_emoji(challenge.difficulty)

    problem_block = (
        f"{diff_icon} <b>#{frontend_id} {title}</b>\n"
        f"Difficulty: <b>{difficulty}</b>\n"
        f"Date: <code>{date}</code>\n"
        f'🔗 <a href="{link}">Open problem</a>'
    )

    if not challenge.user_found:
        return (
            "⚠️ <b>LeetCode · User not found</b>\n"
            "\n"
            "No public LeetCode user matches the configured username.\n"
            "\n"
            f"{problem_block}\n"
            "\n"
            "👉 Set <code>LEETCODE_USERNAME</code> to your public username."
        )

    if challenge.is_done:
        return (
            "✅ <b>LeetCode · Daily done</b>\n"
            "\n"
            f"{problem_block}\n"
            "\n"
            f"Status: <b>DONE</b> · {who}"
        )

    if challenge.cant_verify:
        return (
            "⚠️ <b>LeetCode · Can't verify</b>\n"
            "\n"
            f"{problem_block}\n"
            "\n"
            f"Status: <b>CAN'T VERIFY</b> · 20+ ACs today · {who}\n"
            "Older submissions rolled off public history; verify manually on leetcode.com."
        )

    if challenge.question_status == QSTATUS_NOTAC:
        return (
            "❌ <b>LeetCode · Daily not done</b>\n"
            "\n"
            f"{problem_block}\n"
            "\n"
            f"Status: <b>NOT DONE</b> · attempted today · {who}"
        )

    return (
        "❌ <b>LeetCode · Daily not done</b>\n"
        "\n"
        f"{problem_block}\n"
        "\n"
        f"Status: <b>NOT DONE</b> · {who}"
    )


def format_telegram_error(message: str) -> str:
    return (
        "⚠️ <b>LeetCode · Check failed</b>\n"
        "\n"
        f"<code>{_html_escape(message)}</code>"
    )


def send_telegram(
    text: str,
    *,
    bot_token: str,
    chat_id: str,
    silent: bool,
    timeout: float = 20.0,
) -> None:
    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": silent,
        "disable_web_page_preview": True,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "daily-leetcode-notifier/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise TelegramError(
            f"HTTP {exc.code} from Telegram: {detail[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise TelegramError(f"Network error talking to Telegram: {exc.reason}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TelegramError(f"Invalid JSON from Telegram: {raw[:200]}") from exc

    if not data.get("ok"):
        raise TelegramError(f"Telegram API error: {raw[:300]}")


def resolve_telegram_target() -> tuple[str, str]:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        raise TelegramError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set for --notify "
            "(env or .env)"
        )
    return bot_token, chat_id


def notify_challenge(
    challenge: DailyChallenge,
    *,
    prefer_silent: bool,
    always: bool,
) -> None:
    """
    Send a Telegram message for a successful status fetch.

    Rules:
    - Unknown user: always send, never silent.
    - --notify (default): send only when the daily is still incomplete.
    - --always: also send when the daily is already solved.
    - --silent: quiet delivery (disable_notification); never for user/API errors.
    """
    bot_token, chat_id = resolve_telegram_target()

    if not challenge.user_found:
        send_telegram(
            format_telegram_status(challenge),
            bot_token=bot_token,
            chat_id=chat_id,
            silent=False,  # user/API errors are never silent
        )
        return

    if challenge.is_done and not always:
        return

    send_telegram(
        format_telegram_status(challenge),
        bot_token=bot_token,
        chat_id=chat_id,
        silent=prefer_silent,
    )


def notify_error(message: str) -> None:
    """Errors (API/user lookup) always alert with sound."""
    bot_token, chat_id = resolve_telegram_target()
    send_telegram(
        format_telegram_error(message),
        bot_token=bot_token,
        chat_id=chat_id,
        silent=False,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch today's LeetCode daily challenge and check whether you "
            "have completed today's daily."
        ),
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("LEETCODE_USERNAME"),
        help="Public LeetCode username (default: $LEETCODE_USERNAME)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of human text",
    )
    parser.add_argument(
        "--tags",
        action="store_true",
        help="Show topic tags in human-readable output (hidden by default)",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help=(
            "Send a Telegram message when the daily is still incomplete "
            "(and always on user/API errors). Use --always to also report "
            "when already solved."
        ),
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help=(
            "With --notify: deliver quietly (disable_notification). "
            "User/API errors are never silent. Without --notify this flag is ignored."
        ),
    )
    parser.add_argument(
        "--always",
        action="store_true",
        help=(
            "With --notify: also send Telegram when the daily is already solved. "
            "Without --notify this flag is ignored."
        ),
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file to load if present (default: .env)",
    )
    parser.add_argument(
        "--quiet-ok",
        action="store_true",
        help="Exit 0 with no stdout when the daily is already done (for cron)",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    # Load .env before argparse defaults read env, so --username still wins.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file", default=".env")
    pre_args, _remaining = pre.parse_known_args(argv)
    load_dotenv(Path(pre_args.env_file))

    args = parse_args(argv)
    username = args.username or os.environ.get("LEETCODE_USERNAME")

    if args.silent and not args.notify:
        print(
            "Warning: --silent has no effect without --notify",
            file=sys.stderr,
        )
    if args.always and not args.notify:
        print(
            "Warning: --always has no effect without --notify",
            file=sys.stderr,
        )

    if not username:
        message = "LEETCODE_USERNAME must be set (env or .env)."
        print(f"Error: {message}", file=sys.stderr)
        if args.notify:
            try:
                notify_error(message)
            except TelegramError as tg_exc:
                print(f"Telegram error: {tg_exc}", file=sys.stderr)
                return 2
        return 3

    try:
        challenge = fetch_daily_challenge(username)
    except LeetCodeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.notify:
            try:
                notify_error(str(exc))
            except TelegramError as tg_exc:
                print(f"Telegram error: {tg_exc}", file=sys.stderr)
                return 2
        return 2

    suppress_stdout = (
        args.quiet_ok and challenge.user_found and challenge.is_done
    )
    if not suppress_stdout:
        if args.json:
            payload = asdict(challenge)
            if not args.tags:
                payload.pop("topic_tags", None)
            print(json.dumps(payload, indent=2))
        else:
            print(format_human(challenge, show_tags=args.tags))

    if args.notify:
        try:
            notify_challenge(
                challenge,
                prefer_silent=args.silent,
                always=args.always,
            )
        except TelegramError as tg_exc:
            print(f"Telegram error: {tg_exc}", file=sys.stderr)
            return 2

    if not challenge.user_found:
        return 3  # cannot determine completion
    if challenge.is_done:
        return 0
    return 1  # not done


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point."""
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
