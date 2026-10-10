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
    format_human,
    format_telegram_status,
    is_challenge_done,
    is_solved_in_the_past,
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
        "previously_solved": False,
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
            (True, False, True),
        )

    def test_attempted_today_without_accept(self) -> None:
        subs = [self._sub(status="Wrong Answer")]
        self.assertEqual(
            summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY),
            (False, True, False),
        )

    def test_old_accept_only_is_not_done(self) -> None:
        subs = [self._sub(day="2026-10-09", status="Accepted")]
        self.assertEqual(
            summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY),
            (False, False, True),
        )

    def test_other_problems_ignored(self) -> None:
        subs = [self._sub(slug="three-sum", status="Accepted")]
        self.assertEqual(
            summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY),
            (False, False, False),
        )

    def test_malformed_entries_ignored(self) -> None:
        subs = ["junk", {"titleSlug": self.SLUG}, {"titleSlug": self.SLUG, "timestamp": "xx"}]
        self.assertEqual(
            summarize_daily_submissions(subs, slug=self.SLUG, day=self.DAY),
            (False, False, False),
        )

    def test_username_flag(self) -> None:
        self.assertEqual(parse_args(["--username", "bob"]).username, "bob")
        args = parse_args([])
        for gone in ("session", "csrf", "password", "session_file"):
            self.assertFalse(hasattr(args, gone))

    def _run_with(self, challenge, env=None):
        import os
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            env_file = str(Path(tmp) / ".env")
            with mock.patch.dict(os.environ, env or {}, clear=True):
                with mock.patch(
                    "check_daily.fetch_daily_challenge", return_value=challenge
                ) as fetch:
                    code = run(["--env-file", env_file])
            self.assertEqual(fetch.call_count, 1)
            return code

    def test_run_without_username_returns_3(self) -> None:
        import os
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            env_file = str(Path(tmp) / ".env")
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch(
                    "check_daily.fetch_daily_challenge"
                ) as fetch:
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


if __name__ == "__main__":
    unittest.main()
