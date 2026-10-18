"""Benchmark: the interviewer never asks more than two questions and never loops.

The contract:

* at most two questions are ever produced,
* asking again does not produce more questions (no infinite loop),
* a task that is not multi-step is never questioned,
* the constraint tree records what was found and what is still missing.
"""

from __future__ import annotations

import unittest

from interaction.interviewer import Interviewer

MULTI_STEP_TASK = (
    "Build a blog post about the latest research, then publish it on my "
    "website, and finally share the link on social media"
)

SIMPLE_TASK = "What is the capital of France?"


class InterviewerTests(unittest.TestCase):
    def test_never_more_than_two_questions(self):
        interviewer = Interviewer()
        result = interviewer.ask_questions(MULTI_STEP_TASK)
        self.assertLessEqual(len(result["questions"]), 2)
        self.assertLessEqual(len(result["questions"]), Interviewer.MAX_QUESTIONS)

    def test_repeated_asking_does_not_loop(self):
        interviewer = Interviewer()
        first = interviewer.ask_questions(MULTI_STEP_TASK)
        second = interviewer.ask_questions(MULTI_STEP_TASK)
        third = interviewer.ask_questions(MULTI_STEP_TASK)
        self.assertLessEqual(len(first["questions"]), 2)
        self.assertLessEqual(len(second["questions"]), 2)
        self.assertLessEqual(len(third["questions"]), 2)
        total = (
            len(first["questions"]) + len(second["questions"]) + len(third["questions"])
        )
        self.assertLessEqual(total, 2, "the interviewer must stop after two questions")

    def test_questions_carry_a_default_so_the_pipeline_can_continue(self):
        result = Interviewer().ask_questions(MULTI_STEP_TASK)
        self.assertTrue(result["proceed_with_defaults"])
        for question in result["questions"]:
            self.assertTrue(question["question"].strip())
            self.assertTrue(question["default"].strip())

    def test_simple_task_is_not_questioned(self):
        interviewer = Interviewer()
        self.assertFalse(interviewer.needs_clarification(SIMPLE_TASK))
        self.assertEqual(interviewer.ask_questions(SIMPLE_TASK)["questions"], [])

    def test_known_slots_reduce_the_need_for_questions(self):
        interviewer = Interviewer()
        task = "Build a blog post for developers on the web, then publish it"
        tree = interviewer.extract_constraints(task)
        self.assertIn("platform", tree.filled)
        self.assertFalse(interviewer.needs_clarification(task))

    def test_constraint_tree_is_serialisable_and_complete(self):
        tree = Interviewer().build_constraint_tree(MULTI_STEP_TASK)
        for key in (
            "task", "task_family", "is_multi_step", "slots", "filled_slots",
            "missing_critical", "questions", "defaults", "answers",
        ):
            self.assertIn(key, tree)
        self.assertTrue(tree["is_multi_step"])

    def test_checklist_is_produced_for_the_critic_loop(self):
        from interaction.interviewer import Interviewer as InterviewerClass

        tree = InterviewerClass().extract_constraints(MULTI_STEP_TASK)
        checklist = tree.checklist()
        self.assertTrue(checklist)
        for item in checklist:
            self.assertIn("name", item)
            self.assertIn("status", item)
            self.assertIn(item["status"], {"pass", "fail", "info"})

    def test_slot_patterns_extract_money_and_dates(self):
        tree = Interviewer().extract_constraints(
            "Write a launch post for 2026-04-01 with a budget under Rs.50,000"
        )
        self.assertIn("date", tree.filled)
        self.assertIn("money", tree.filled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
