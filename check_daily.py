#!/usr/bin/env python3
"""
Check LeetCode's daily coding challenge and whether the authenticated user
has already solved it.

Optional Telegram alerts via --notify (use --silent for quiet deliveries).
Authentication errors are never sent as silent Telegram notifications.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
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

USER_STATUS_QUERY = """
query {
  userStatus {
    isSignedIn
    username
    realName
    userSlug
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
    # Authenticated fields
    daily_user_status: str | None  # NotStart | Finish | ...
    question_status: str | None  # ac | notac | None
    is_done: bool
    username: str | None
    is_signed_in: bool


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
    session: str | None = None,
    csrf: str | None = None,
    variables: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
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

    cookies: list[str] = []
    if session:
        cookies.append(f"LEETCODE_SESSION={session}")
    if csrf:
        cookies.append(f"csrftoken={csrf}")
        headers["x-csrftoken"] = csrf
    if cookies:
        headers["Cookie"] = "; ".join(cookies)

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


def fetch_user_status(
    session: str | None, csrf: str | None
) -> tuple[bool, str | None]:
    if not session:
        return False, None
    data = graphql(USER_STATUS_QUERY, session=session, csrf=csrf)
    status = data.get("userStatus") or {}
    is_signed_in = bool(status.get("isSignedIn"))
    username = status.get("username") or status.get("userSlug") or None
    if username == "":
        username = None
    return is_signed_in, username


def is_challenge_done(daily_user_status: str | None, question_status: str | None) -> bool:
    """
    Decide completion from authenticated fields.

    Prefer the daily challenge userStatus (Finish). Fall back to the problem's
    general status (ac) so already-solved problems still count.
    """
    if daily_user_status and daily_user_status.lower() == STATUS_FINISH.lower():
        return True
    if question_status and question_status.lower() == QSTATUS_AC:
        return True
    return False


def fetch_daily_challenge(
    session: str | None = None,
    csrf: str | None = None,
) -> DailyChallenge:
    is_signed_in, username = fetch_user_status(session, csrf)

    data = graphql(DAILY_QUERY, session=session, csrf=csrf)
    node = data.get("activeDailyCodingChallengeQuestion")
    if not node:
        raise LeetCodeError("No active daily coding challenge returned.")

    question = node.get("question") or {}
    tags = [t.get("name") for t in (question.get("topicTags") or []) if t.get("name")]

    daily_user_status = node.get("userStatus")
    question_status = question.get("status")
    link_path = node.get("link") or f"/problems/{question.get('titleSlug', '')}/"
    if not link_path.startswith("http"):
        link = f"{LEETCODE_ORIGIN}{link_path}"
    else:
        link = link_path

    done = False
    if is_signed_in:
        done = is_challenge_done(daily_user_status, question_status)
    else:
        # Without auth, userStatus is always NotStart and is not meaningful.
        daily_user_status = None
        question_status = None

    return DailyChallenge(
        date=node.get("date") or "",
        title=question.get("title") or "",
        title_slug=question.get("titleSlug") or "",
        difficulty=question.get("difficulty") or "Unknown",
        frontend_id=str(question.get("questionFrontendId") or ""),
        link=link,
        ac_rate=question.get("acRate"),
        topic_tags=tags,
        daily_user_status=daily_user_status,
        question_status=question_status,
        is_done=done,
        username=username,
        is_signed_in=is_signed_in,
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

    if not challenge.is_signed_in:
        lines.append("User:       (not signed in)")
        lines.append("Status:     UNKNOWN — set LEETCODE_SESSION to check completion")
        lines.append("")
        lines.append(
            "Tip: copy the LEETCODE_SESSION cookie from your browser while "
            "logged into leetcode.com and export it, or put it in a .env file."
        )
    else:
        who = challenge.username or "(signed in)"
        lines.append(f"User:       {who}")
        if challenge.is_done:
            lines.append("Status:     DONE ✓  — daily challenge already solved")
        else:
            detail = challenge.daily_user_status or challenge.question_status or "NotStart"
            if challenge.question_status == QSTATUS_NOTAC:
                lines.append(
                    f"Status:     NOT DONE  — attempted but not accepted ({detail})"
                )
            else:
                lines.append(f"Status:     NOT DONE  — not solved yet ({detail})")

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

    if not challenge.is_signed_in:
        return (
            "⚠️ <b>LeetCode · Session invalid</b>\n"
            "\n"
            "Your <code>LEETCODE_SESSION</code> cookie is missing, expired, "
            "or not accepted.\n"
            "\n"
            f"{problem_block}\n"
            "\n"
            "👉 Refresh the cookie in <code>.env</code> and re-run."
        )

    if challenge.is_done:
        return (
            "✅ <b>LeetCode · Daily done</b>\n"
            "\n"
            f"{problem_block}\n"
            "\n"
            f"Status: <b>DONE</b> · {who}"
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
) -> None:
    """
    Send a Telegram message for a successful status fetch.

    Rules:
    - Auth invalid: always send, never silent.
    - Already solved: never notify (silent or not).
    - Not done + --silent: quiet reminder.
    - Not done without --silent: alert with sound.
    """
    bot_token, chat_id = resolve_telegram_target()

    if not challenge.is_signed_in:
        send_telegram(
            format_telegram_status(challenge),
            bot_token=bot_token,
            chat_id=chat_id,
            silent=False,  # auth errors are never silent
        )
        return

    # No ping once the daily is already solved
    if challenge.is_done:
        return

    send_telegram(
        format_telegram_status(challenge),
        bot_token=bot_token,
        chat_id=chat_id,
        silent=prefer_silent,
    )


def notify_error(message: str) -> None:
    """Errors (API/auth plumbing) always alert with sound."""
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
            "have already solved it."
        ),
    )
    parser.add_argument(
        "--session",
        default=os.environ.get("LEETCODE_SESSION"),
        help="LEETCODE_SESSION cookie value (default: $LEETCODE_SESSION)",
    )
    parser.add_argument(
        "--csrf",
        default=os.environ.get("LEETCODE_CSRFTOKEN") or os.environ.get("CSRFTOKEN"),
        help="Optional csrftoken cookie (default: $LEETCODE_CSRFTOKEN)",
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
        help="Send a Telegram message about the result",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help=(
            "With --notify: if the daily is still open, deliver quietly "
            "(disable_notification). Skips Telegram entirely when already done. "
            "Auth/API errors are never silent. Without --notify this flag is ignored."
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
    # Load .env before argparse defaults re-read env in parse_args path:
    # we load first, then parse so --session still wins over env.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file", default=".env")
    pre_args, _remaining = pre.parse_known_args(argv)
    load_dotenv(Path(pre_args.env_file))

    args = parse_args(argv)
    # Re-bind session/csrf after dotenv load if CLI did not override
    session = args.session or os.environ.get("LEETCODE_SESSION")
    csrf = (
        args.csrf
        or os.environ.get("LEETCODE_CSRFTOKEN")
        or os.environ.get("CSRFTOKEN")
    )

    if args.silent and not args.notify:
        print(
            "Warning: --silent has no effect without --notify",
            file=sys.stderr,
        )

    try:
        challenge = fetch_daily_challenge(session=session, csrf=csrf)
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
        args.quiet_ok and challenge.is_signed_in and challenge.is_done
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
            notify_challenge(challenge, prefer_silent=args.silent)
        except TelegramError as tg_exc:
            print(f"Telegram error: {tg_exc}", file=sys.stderr)
            return 2

    if not challenge.is_signed_in:
        return 3  # cannot determine completion
    if challenge.is_done:
        return 0
    return 1  # not done


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point."""
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
