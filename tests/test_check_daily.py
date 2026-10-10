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
    LogoutError,
    extract_cookie_value,
    format_human,
    format_telegram_status,
    is_challenge_done,
    is_solved_in_the_past,
    login_with_password,
    logout_quietly,
    logout_session,
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
        # No session is configured or cached: these options must not exist.
        args = parse_args([])
        self.assertFalse(hasattr(args, "session"))
        self.assertFalse(hasattr(args, "csrf"))
        self.assertFalse(hasattr(args, "auto_refresh"))
        self.assertFalse(hasattr(args, "save_session"))
        self.assertFalse(hasattr(args, "session_file"))

    def test_run_without_credentials_returns_3(self) -> None:
        import os
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            env_file = str(Path(tmp) / ".env")
            clean = {
                k: v
                for k, v in os.environ.items()
                if k not in ("LEETCODE_USERNAME", "LEETCODE_PASSWORD")
            }
            with (
                mock.patch.dict(os.environ, clean, clear=True),
                mock.patch("check_daily.login_with_password") as login,
            ):
                code = run(["--env-file", env_file])
            self.assertEqual(code, 3)
            login.assert_not_called()

    def test_run_logs_in_checks_out_and_logs_out(self) -> None:
        import os
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            env_file = str(Path(tmp) / ".env")
            with mock.patch.dict(
                os.environ,
                {"LEETCODE_USERNAME": "u", "LEETCODE_PASSWORD": "p"},
                clear=True,
            ):
                with (
                    mock.patch(
                        "check_daily.login_with_password",
                        return_value=("sess", "csrf"),
                    ) as login,
                    mock.patch(
                        "check_daily.fetch_daily_challenge",
                        return_value=_challenge(),
                    ) as fetch,
                    mock.patch("check_daily.logout_session") as logout,
                ):
                    code = run(["--env-file", env_file])
            self.assertEqual(code, 1)  # signed in, not done
            login.assert_called_once_with("u", "p")
            fetch.assert_called_once_with(session="sess", csrf="csrf")
            logout.assert_called_once_with("sess", "csrf")

    def test_run_logs_out_even_when_check_fails(self) -> None:
        import os
        from unittest import mock

        from check_daily import LeetCodeError

        with tempfile.TemporaryDirectory() as tmp:
            env_file = str(Path(tmp) / ".env")
            with mock.patch.dict(
                os.environ,
                {"LEETCODE_USERNAME": "u", "LEETCODE_PASSWORD": "p"},
                clear=True,
            ):
                with (
                    mock.patch(
                        "check_daily.login_with_password",
                        return_value=("sess", "csrf"),
                    ),
                    mock.patch(
                        "check_daily.fetch_daily_challenge",
                        side_effect=LeetCodeError("boom"),
                    ),
                    mock.patch("check_daily.logout_session") as logout,
                ):
                    code = run(["--env-file", env_file])
            self.assertEqual(code, 2)
            logout.assert_called_once_with("sess", "csrf")

    def test_logout_failure_never_fails_run(self) -> None:
        from unittest import mock

        with mock.patch(
            "check_daily.logout_session", side_effect=LogoutError("nope")
        ):
            # Must not raise: logout is best-effort.
            logout_quietly("sess", "csrf")
        # Empty session is a no-op (also must not raise / call out).
        with mock.patch("check_daily.logout_session") as logout:
            logout_quietly(None, None)
            logout.assert_not_called()


if __name__ == "__main__":
    unittest.main()
