#!/usr/bin/env python3
"""Unit tests for daily-challenge completion and status copy."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from check_daily import (
    DailyChallenge,
    LoginError,
    extract_cookie_value,
    format_human,
    format_telegram_status,
    is_challenge_done,
    is_solved_in_the_past,
    login_with_password,
    parse_args,
    run,
)


def _challenge(**overrides: object) -> DailyChallenge:
    data: dict[str, object] = {
        "date": "2026-09-14",
        "title": "Two Sum",
        "title_slug": "two-sum",
        "difficulty": "Easy",
        "frontend_id": "1",
        "link": "https://leetcode.com/problems/two-sum/",
        "ac_rate": 50.0,
        "topic_tags": ["Array"],
        "daily_user_status": "NotStart",
        "question_status": None,
        "is_done": False,
        "previously_solved": False,
        "username": "alice",
        "is_signed_in": True,
    }
    data.update(overrides)
    return DailyChallenge(**data)  # type: ignore[arg-type]


class ChallengeDoneTests(unittest.TestCase):
    def test_finish_is_done(self) -> None:
        self.assertTrue(is_challenge_done("Finish"))
        self.assertTrue(is_challenge_done("finish"))

    def test_not_start_is_not_done(self) -> None:
        self.assertFalse(is_challenge_done("NotStart"))
        self.assertFalse(is_challenge_done(None))
        self.assertFalse(is_challenge_done(""))

    def test_lifetime_ac_does_not_count_as_daily(self) -> None:
        # Old accepted submissions must not mark today's daily complete.
        self.assertFalse(is_challenge_done("NotStart"))
        self.assertTrue(is_solved_in_the_past("NotStart", "ac"))
        self.assertTrue(is_solved_in_the_past("NotStart", "AC"))

    def test_finish_is_not_solved_in_the_past(self) -> None:
        self.assertFalse(is_solved_in_the_past("Finish", "ac"))

    def test_never_solved_is_not_solved_in_the_past(self) -> None:
        self.assertFalse(is_solved_in_the_past("NotStart", None))
        self.assertFalse(is_solved_in_the_past("NotStart", "notac"))


class StatusCopyTests(unittest.TestCase):
    def test_human_hints_done_in_the_past(self) -> None:
        text = format_human(
            _challenge(
                daily_user_status="NotStart",
                question_status="ac",
                is_done=False,
                previously_solved=True,
            )
        )
        self.assertIn("NOT DONE", text)
        self.assertIn("done in the past", text)
        self.assertIn("submit again for today's daily", text)

    def test_telegram_hints_done_in_the_past(self) -> None:
        text = format_telegram_status(
            _challenge(
                daily_user_status="NotStart",
                question_status="ac",
                is_done=False,
                previously_solved=True,
            )
        )
        self.assertIn("Daily not done", text)
        self.assertIn("done in the past", text)
        self.assertIn("Submit again to get today's credit.", text)

    def test_human_never_solved_has_no_past_hint(self) -> None:
        text = format_human(_challenge())
        self.assertIn("NOT DONE", text)
        self.assertIn("not solved yet", text)
        self.assertNotIn("done in the past", text)

    def test_done_status_has_no_past_hint(self) -> None:
        challenge = _challenge(
            daily_user_status="Finish",
            question_status="ac",
            is_done=True,
            previously_solved=False,
        )
        self.assertNotIn("done in the past", format_human(challenge))
        self.assertNotIn("done in the past", format_telegram_status(challenge))


class PasswordLoginTests(unittest.TestCase):
    def test_extract_cookie_value(self) -> None:
        headers = [
            "csrftoken=abc123; expires=Sun, 11-Oct-2026 12:00:00 GMT; Path=/",
            "LEETCODE_SESSION=eyJzZXNzaW9uIjoidGVzdCJ9; HttpOnly; Path=/",
        ]
        self.assertEqual(extract_cookie_value(headers, "csrftoken"), "abc123")
        self.assertEqual(
            extract_cookie_value(headers, "LEETCODE_SESSION"),
            "eyJzZXNzaW9uIjoidGVzdCJ9",
        )
        self.assertIsNone(extract_cookie_value(headers, "missing"))

    def test_login_requires_credentials(self) -> None:
        with self.assertRaises(LoginError):
            login_with_password("", "")
        with self.assertRaises(LoginError):
            login_with_password("user", "")

    def test_no_session_flags(self) -> None:
        # No session is stored: these options must not exist anymore.
        args = parse_args([])
        self.assertFalse(hasattr(args, "session"))
        self.assertFalse(hasattr(args, "csrf"))
        self.assertFalse(hasattr(args, "auto_refresh"))
        self.assertFalse(hasattr(args, "save_session"))

    def test_run_without_credentials_returns_3(self) -> None:
        import os
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            env_file = str(Path(tmp) / ".env")
            clean = {
                k: v
                for k, v in os.environ.items()
                if k
                not in (
                    "LEETCODE_USERNAME",
                    "LEETCODE_PASSWORD",
                    "LEETCODE_SESSION",
                    "LEETCODE_CSRFTOKEN",
                    "CSRFTOKEN",
                )
            }
            with mock.patch.dict(os.environ, clean, clear=True):
                code = run(["--env-file", env_file])
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
