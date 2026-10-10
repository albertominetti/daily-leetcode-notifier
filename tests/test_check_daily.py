#!/usr/bin/env python3
"""Unit tests for daily-challenge completion and status copy."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from check_daily import (
    DailyChallenge,
    LeetCodeError,
    TelegramError,
    format_human,
    format_telegram_status,
    is_challenge_done,
    load_dotenv,
    notify_challenge,
    notify_error,
    parse_args,
    run,
    summarize_daily_submissions,
    utc_day,
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
        "username": "alice",
        "user_found": True,
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


class StatusCopyTests(unittest.TestCase):
    def test_human_attempted_today(self) -> None:
        text = format_human(
            _challenge(
                daily_user_status="NotStart",
                question_status="notac",
                is_done=False,
            )
        )
        self.assertIn("NOT DONE", text)
        self.assertIn("attempted today but not accepted", text)

    def test_telegram_attempted_today(self) -> None:
        text = format_telegram_status(
            _challenge(
                daily_user_status="NotStart",
                question_status="notac",
                is_done=False,
            )
        )
        self.assertIn("Daily not done", text)
        self.assertIn("attempted today", text)

    def test_human_not_solved_today(self) -> None:
        text = format_human(_challenge())
        self.assertIn("NOT DONE", text)
        self.assertIn("not solved today", text)

    def test_done_status(self) -> None:
        challenge = _challenge(
            daily_user_status="Finish",
            question_status="ac",
            is_done=True,
        )
        self.assertIn("DONE ✓", format_human(challenge))
        self.assertIn("Daily done", format_telegram_status(challenge))

    def test_cant_verify_warning_in_outputs(self) -> None:
        challenge = _challenge(
            is_done=False,
            cant_verify=True,
            daily_user_status=None,
            question_status=None,
        )
        human = format_human(challenge)
        self.assertIn("CAN'T VERIFY", human)
        self.assertIn("20+ accepted submissions today", human)

        tg = format_telegram_status(challenge)
        self.assertIn("Can't verify", tg)
        self.assertIn("20+ ACs today", tg)


class PublicCheckTests(unittest.TestCase):
    DAY = "2026-10-10"
    SLUG = "two-sum"

    def _sub(self, slug=None, day="2026-10-10", status="Accepted"):
        import calendar
        from datetime import datetime, timezone

        ts = str(
            calendar.timegm(datetime(2026, 10, 10, tzinfo=timezone.utc).timetuple())
            if day == "2026-10-10"
            else calendar.timegm(
                datetime(2026, 10, 9, tzinfo=timezone.utc).timetuple()
            )
        )
        return {"titleSlug": slug or self.SLUG, "timestamp": ts, "statusDisplay": status}

    def test_utc_day(self) -> None:
        self.assertEqual(utc_day("1791086655"), "2026-10-04")
        self.assertIsNone(utc_day("not-a-number"))
        self.assertIsNone(utc_day(""))

    def test_accepted_today_is_done(self) -> None:
        subs = [self._sub(status="Wrong Answer"), self._sub(status="Accepted")]
        self.assertEqual(
            summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY),
            (True, False),
        )

    def test_attempted_today_without_accept(self) -> None:
        subs = [self._sub(status="Wrong Answer")]
        self.assertEqual(
            summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY),
            (False, True),
        )

    def test_old_accept_only_is_not_done(self) -> None:
        subs = [self._sub(day="2026-10-09", status="Accepted")]
        self.assertEqual(
            summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY),
            (False, False),
        )

    def test_other_problems_ignored(self) -> None:
        subs = [self._sub(slug="three-sum", status="Accepted")]
        self.assertEqual(
            summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY),
            (False, False),
        )

    def test_malformed_entries_ignored(self) -> None:
        subs = ["junk", {"titleSlug": self.SLUG}, {"titleSlug": self.SLUG, "timestamp": "xx"}]
        self.assertEqual(
            summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY),
            (False, False),
        )

    def test_username_flag(self) -> None:
        self.assertEqual(parse_args(["--username", "bob"]).username, "bob")
        args = parse_args([])
        for gone in ("session", "csrf", "password", "session_file"):
            self.assertFalse(hasattr(args, gone))

    def _run_with(self, challenge, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = str(Path(tmp) / ".env")
            with mock.patch.dict(os.environ, env or {}, clear=True):
                with (
                    mock.patch("sys.stdout", io.StringIO()),
                    mock.patch("sys.stderr", io.StringIO()),
                    mock.patch(
                        "check_daily.fetch_daily_challenge", return_value=challenge
                    ) as fetch,
                ):
                    code = run(["--env-file", env_file])
            self.assertEqual(fetch.call_count, 1)
            return code

    def test_run_without_username_returns_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = str(Path(tmp) / ".env")
            with mock.patch.dict(os.environ, {}, clear=True):
                with (
                    mock.patch("sys.stdout", io.StringIO()),
                    mock.patch("sys.stderr", io.StringIO()),
                    mock.patch("check_daily.fetch_daily_challenge") as fetch,
                ):
                    code = run(["--env-file", env_file])
        self.assertEqual(code, 3)
        fetch.assert_not_called()

    def test_run_exit_codes(self) -> None:
        env = {"LEETCODE_USERNAME": "alice"}
        done = _challenge(is_done=True, daily_user_status="Finish", question_status="ac")
        self.assertEqual(self._run_with(done, env), 0)
        self.assertEqual(self._run_with(_challenge(), env), 1)
        unknown = _challenge(
            is_done=False, user_found=False, username=None,
            daily_user_status=None, question_status=None,
        )
        self.assertEqual(self._run_with(unknown, env), 3)

    def test_fetch_daily_challenge_merges_ac_list(self) -> None:
        from unittest import mock

        import check_daily

        fake_daily = {
            "activeDailyCodingChallengeQuestion": {
                "date": "2026-10-10",
                "question": {
                    "questionFrontendId": "1",
                    "title": "Two Sum",
                    "titleSlug": "two-sum",
                    "difficulty": "Easy",
                    "acRate": 50.0,
                    "topicTags": [],
                },
            }
        }
        fake_user = {
            "matchedUser": {"username": "bob"},
            # recentSubmissionList only has a non-AC attempt
            "recentSubmissionList": [
                {"titleSlug": "two-sum", "timestamp": "1791633600", "statusDisplay": "Wrong Answer"}
            ],
            # but recentAcSubmissionList has the Accepted solution
            "recentAcSubmissionList": [
                {"titleSlug": "two-sum", "timestamp": "1791633600"}
            ],
        }

        with mock.patch("check_daily.graphql", side_effect=[fake_daily, fake_user]):
            c = check_daily.fetch_daily_challenge("bob")
        self.assertTrue(c.is_done)
        self.assertEqual(c.daily_user_status, "Finish")
        self.assertFalse(c.cant_verify)

    def test_cant_verify_when_20_or_more_acs_today(self) -> None:
        from unittest import mock

        import check_daily

        fake_daily = {
            "activeDailyCodingChallengeQuestion": {
                "date": "2026-10-10",
                "question": {
                    "questionFrontendId": "1",
                    "title": "Two Sum",
                    "titleSlug": "two-sum",
                    "difficulty": "Easy",
                    "acRate": 50.0,
                    "topicTags": [],
                },
            }
        }
        # 20 accepted submissions today, all on DIFFERENT problems
        twenty_acs = [
            {"titleSlug": f"problem-{i}", "timestamp": "1791633600"}
            for i in range(20)
        ]
        fake_user = {
            "matchedUser": {"username": "bob"},
            "recentSubmissionList": [
                {**s, "statusDisplay": "Accepted"} for s in twenty_acs
            ],
            "recentAcSubmissionList": twenty_acs,
        }

        with mock.patch("check_daily.graphql", side_effect=[fake_daily, fake_user]):
            c = check_daily.fetch_daily_challenge("bob")
        self.assertFalse(c.is_done)
        self.assertTrue(c.cant_verify)


class SubmissionEdgeCasesTests(unittest.TestCase):
    DAY = "2026-10-10"
    SLUG = "two-sum"

    def test_ac_after_failures_is_done(self) -> None:
        subs = [
            {"titleSlug": self.SLUG, "timestamp": "1791633600", "statusDisplay": "Wrong Answer"},
            {"titleSlug": self.SLUG, "timestamp": "1791633610", "statusDisplay": "Time Limit Exceeded"},
            {"titleSlug": self.SLUG, "timestamp": "1791633620", "statusDisplay": "Accepted"},
        ]
        done, attempted = summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY)
        self.assertTrue(done)
        self.assertFalse(attempted)

    def test_failure_after_ac_still_done(self) -> None:
        subs = [
            {"titleSlug": self.SLUG, "timestamp": "1791633600", "statusDisplay": "Accepted"},
            {"titleSlug": self.SLUG, "timestamp": "1791633650", "statusDisplay": "Wrong Answer"},
        ]
        done, attempted = summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY)
        self.assertTrue(done)
        self.assertFalse(attempted)

    def test_utc_midnight_boundaries(self) -> None:
        # 2026-10-10 00:00:00 UTC = 1791590400
        # 2026-10-10 23:59:59 UTC = 1791676799
        self.assertEqual(utc_day(1791590399), "2026-10-09")  # 1s before midnight
        self.assertEqual(utc_day(1791590400), "2026-10-10")  # exactly midnight start
        self.assertEqual(utc_day(1791676799), "2026-10-10")  # 1s before next day
        self.assertEqual(utc_day(1791676800), "2026-10-11")  # next day midnight start


class NotificationDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = {
            "TELEGRAM_BOT_TOKEN": "token-123",
            "TELEGRAM_CHAT_ID": "chat-456",
        }

    def test_notify_skips_when_done_without_always(self) -> None:
        challenge = _challenge(is_done=True, daily_user_status="Finish", question_status="ac")
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("check_daily.send_telegram") as send:
                notify_challenge(challenge, prefer_silent=False, always=False)
        send.assert_not_called()

    def test_notify_sends_when_done_with_always(self) -> None:
        challenge = _challenge(is_done=True, daily_user_status="Finish", question_status="ac")
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("check_daily.send_telegram") as send:
                notify_challenge(challenge, prefer_silent=False, always=True)
        send.assert_called_once()
        self.assertFalse(send.call_args.kwargs["silent"])

    def test_notify_sends_when_done_with_always_and_silent(self) -> None:
        challenge = _challenge(is_done=True, daily_user_status="Finish", question_status="ac")
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("check_daily.send_telegram") as send:
                notify_challenge(challenge, prefer_silent=True, always=True)
        send.assert_called_once()
        self.assertTrue(send.call_args.kwargs["silent"])

    def test_notify_sends_when_not_done(self) -> None:
        challenge = _challenge(is_done=False)
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("check_daily.send_telegram") as send:
                notify_challenge(challenge, prefer_silent=False, always=False)
        send.assert_called_once()
        self.assertFalse(send.call_args.kwargs["silent"])

    def test_notify_sends_when_not_done_silent(self) -> None:
        challenge = _challenge(is_done=False)
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("check_daily.send_telegram") as send:
                notify_challenge(challenge, prefer_silent=True, always=False)
        send.assert_called_once()
        self.assertTrue(send.call_args.kwargs["silent"])

    def test_user_not_found_always_alerts_never_silent(self) -> None:
        challenge = _challenge(user_found=False, username=None)
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("check_daily.send_telegram") as send:
                notify_challenge(challenge, prefer_silent=True, always=False)
        send.assert_called_once()
        # Even with prefer_silent=True, user lookup errors alert with sound
        self.assertFalse(send.call_args.kwargs["silent"])

    def test_notify_error_always_alerts_never_silent(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch("check_daily.send_telegram") as send:
                notify_error("Network down")
        send.assert_called_once()
        self.assertFalse(send.call_args.kwargs["silent"])

    def test_notify_missing_credentials_raises(self) -> None:
        challenge = _challenge(is_done=False)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TelegramError):
                notify_challenge(challenge, prefer_silent=False, always=False)


class CliFlagsAndOutputTests(unittest.TestCase):
    def test_quiet_ok_suppresses_stdout_when_done(self) -> None:
        challenge = _challenge(is_done=True, daily_user_status="Finish", question_status="ac")
        buf = io.StringIO()
        with (
            mock.patch("sys.stdout", buf),
            mock.patch("check_daily.fetch_daily_challenge", return_value=challenge),
        ):
            code = run(["--username", "alice", "--quiet-ok", "--env-file", "/tmp/none.env"])
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "")

    def test_quiet_ok_does_not_suppress_stdout_when_not_done(self) -> None:
        challenge = _challenge(is_done=False)
        buf = io.StringIO()
        with (
            mock.patch("sys.stdout", buf),
            mock.patch("check_daily.fetch_daily_challenge", return_value=challenge),
        ):
            code = run(["--username", "alice", "--quiet-ok", "--env-file", "/tmp/none.env"])
        self.assertEqual(code, 1)
        self.assertIn("NOT DONE", buf.getvalue())

    def test_json_output_format(self) -> None:
        challenge = _challenge(topic_tags=["Array", "Hash Table"])
        buf = io.StringIO()
        with (
            mock.patch("sys.stdout", buf),
            mock.patch("check_daily.fetch_daily_challenge", return_value=challenge),
        ):
            code = run(["--username", "alice", "--json", "--env-file", "/tmp/none.env"])
        self.assertEqual(code, 1)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["title"], "Two Sum")
        self.assertNotIn("topic_tags", data)

    def test_json_output_with_tags(self) -> None:
        challenge = _challenge(topic_tags=["Array", "Hash Table"])
        buf = io.StringIO()
        with (
            mock.patch("sys.stdout", buf),
            mock.patch("check_daily.fetch_daily_challenge", return_value=challenge),
        ):
            code = run(["--username", "alice", "--json", "--tags", "--env-file", "/tmp/none.env"])
        self.assertEqual(code, 1)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["topic_tags"], ["Array", "Hash Table"])


class DotenvAndErrorHandlingTests(unittest.TestCase):
    def test_load_dotenv_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "# Sample comment\n"
                "TEST_KEY1=alpha\n"
                "TEST_KEY2='single quoted'\n"
                "TEST_KEY3=\"double quoted\"\n"
                "INVALID_LINE\n"
                "EXISTING_KEY=new_val\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"EXISTING_KEY": "original_val"}, clear=True):
                load_dotenv(env_path)
                self.assertEqual(os.environ.get("TEST_KEY1"), "alpha")
                self.assertEqual(os.environ.get("TEST_KEY2"), "single quoted")
                self.assertEqual(os.environ.get("TEST_KEY3"), "double quoted")
                # Existing key must NOT be overwritten
                self.assertEqual(os.environ.get("EXISTING_KEY"), "original_val")

    def test_run_handles_leetcode_error_exit_2(self) -> None:
        with (
            mock.patch("sys.stderr", io.StringIO()),
            mock.patch("check_daily.fetch_daily_challenge", side_effect=LeetCodeError("API timed out")),
        ):
            code = run(["--username", "alice", "--env-file", "/tmp/none.env"])
        self.assertEqual(code, 2)

    def test_run_notifies_telegram_on_leetcode_error(self) -> None:
        with (
            mock.patch("sys.stderr", io.StringIO()),
            mock.patch("check_daily.fetch_daily_challenge", side_effect=LeetCodeError("API timed out")),
            mock.patch("check_daily.notify_error") as notify_err,
        ):
            code = run(["--username", "alice", "--notify", "--env-file", "/tmp/none.env"])
        self.assertEqual(code, 2)
        notify_err.assert_called_once_with("API timed out")


if __name__ == "__main__":
    unittest.main()
