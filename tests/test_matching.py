"""Tests for the matching rules. These use fixed inputs, so the results are predictable.

Run from the EditMatch folder with:
    python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

# Make the project folder importable when this file is run from the tests folder.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matching_service as matching  # noqa: E402


def fortnite_job():
    return {
        "game": "Fortnite",
        "category": "Gaming",
        "effects": ["Velocity editing", "Beat synchronisation"],
        "budget_max": 2000,
        "delivery_hours": 48,
    }


def fortnite_editor(**overrides):
    editor = {
        "id": 1,
        "name": "Aarav",
        "games": ["Fortnite"],
        "categories": ["Gaming"],
        "skill_tags": ["Velocity editing", "Beat synchronisation"],
        "samples": [{
            "title": "Fortnite Velocity Edit", "category": "Gaming", "game": "Fortnite",
            "skill_tags": ["Velocity editing"], "description": "",
        }],
        "minimum_price": 1500,
        "availability": "Available",
        "delivery_hours": 24,
        "public": None,
    }
    editor.update(overrides)
    return editor


def podcast_editor():
    return {
        "id": 2,
        "name": "Ishita",
        "games": [],
        "categories": ["Podcast editing"],
        "skill_tags": ["Sound design"],
        "samples": [],
        "minimum_price": 500,
        "availability": "Busy",
        "delivery_hours": 72,
        "public": None,
    }


class CategoryAndGameTests(unittest.TestCase):
    def test_fortnite_request_matches_editor_who_lists_fortnite(self):
        result = matching.calculate_editor_match_score(fortnite_job(), fortnite_editor())
        self.assertEqual(result["points"]["category_and_game"], 30)

    def test_editor_without_matching_category_does_not_get_a_perfect_score(self):
        result = matching.calculate_editor_match_score(fortnite_job(), podcast_editor())
        self.assertEqual(result["points"]["category_and_game"], 0)
        self.assertLess(result["score"], 100)


class EffectTests(unittest.TestCase):
    def test_velocity_request_matches_editor_who_declares_velocity_editing(self):
        job = {"effects": ["Velocity editing"]}
        result = matching.calculate_editor_match_score(job, fortnite_editor())
        self.assertEqual(result["points"]["effects_and_techniques"], 25)
        self.assertTrue(any("Velocity editing" in s for s in result["strengths"]))


class BudgetTests(unittest.TestCase):
    def test_editor_above_maximum_budget_receives_a_penalty(self):
        affordable = matching.calculate_editor_match_score(fortnite_job(), fortnite_editor(minimum_price=1500))
        expensive = matching.calculate_editor_match_score(fortnite_job(), fortnite_editor(minimum_price=5000))
        self.assertEqual(expensive["points"]["budget"], 0)
        self.assertEqual(expensive["penalties"]["budget_mismatch"], matching.BUDGET_MISMATCH_PENALTY)
        self.assertLess(expensive["score"], affordable["score"])
        self.assertTrue(any("above your" in m for m in expensive["mismatches"]))

    def test_missing_price_is_unknown_not_free(self):
        result = matching.calculate_editor_match_score(fortnite_job(), fortnite_editor(minimum_price=None))
        self.assertEqual(result["points"]["budget"], 20 * matching.UNKNOWN_SHARE)
        self.assertNotIn("budget_mismatch", result["penalties"])
        self.assertTrue(any("not stated" in u for u in result["unknowns"]))


class AvailabilityAndDeadlineTests(unittest.TestCase):
    def test_missing_availability_gets_no_points(self):
        result = matching.calculate_editor_match_score(fortnite_job(), fortnite_editor(availability=""))
        self.assertEqual(result["points"]["availability"], 0)
        self.assertEqual(result["score"], 90)

    def test_deadline_conflict_is_penalised(self):
        result = matching.calculate_editor_match_score(fortnite_job(), fortnite_editor(delivery_hours=168))
        self.assertEqual(result["points"]["deadline"], 0)
        self.assertTrue(any("longer than" in m for m in result["mismatches"]))


class NormalisationTests(unittest.TestCase):
    def test_synonyms_are_normalised(self):
        self.assertEqual(matching.normalise_tag("VFX"), matching.normalise_tag("visual effects"))
        self.assertEqual(matching.normalise_tag("Beat Sync"), matching.normalise_tag("beat synchronization"))
        self.assertEqual(matching.normalise_tag("Color grading"), matching.normalise_tag("Colour grading"))

    def test_duplicate_tags_are_merged(self):
        tags = matching.normalise_skill_tags("Beat Sync, beat synchronisation, Velocity edit")
        self.assertEqual(tags, ["beat synchronisation", "velocity editing"])


class ExplanationTests(unittest.TestCase):
    def test_explanation_does_not_claim_unsupported_requirements(self):
        editor = podcast_editor()
        result = matching.calculate_editor_match_score(fortnite_job(), editor)
        explanation = matching.explain_match(result)
        self.assertNotIn("Fortnite", explanation["summary"])
        self.assertNotIn("Velocity", explanation["summary"])

    def test_explanation_uses_the_editors_real_data(self):
        result = matching.calculate_editor_match_score(fortnite_job(), fortnite_editor())
        explanation = matching.explain_match(result)
        self.assertIn("Strong match", explanation["summary"])
        self.assertIn("Fortnite", explanation["summary"])


class DeterminismTests(unittest.TestCase):
    def test_same_inputs_give_same_result(self):
        first = matching.calculate_editor_match_score(fortnite_job(), fortnite_editor())
        second = matching.calculate_editor_match_score(fortnite_job(), fortnite_editor())
        self.assertEqual(first, second)

    def test_full_example_scores_95(self):
        # category 30 + effects 25 + budget 20 + deadline 10 + one matching sample 5 + available 5
        result = matching.calculate_editor_match_score(fortnite_job(), fortnite_editor())
        self.assertEqual(result["score"], 95)

    def test_ranking_puts_best_editor_first(self):
        ranked = matching.get_top_matching_editors(fortnite_job(), [podcast_editor(), fortnite_editor()])
        self.assertEqual(ranked[0]["editor"]["name"], "Aarav")


class ParserTests(unittest.TestCase):
    def test_parser_finds_common_requirements(self):
        parsed = matching.parse_project_description(
            "I need a Fortnite montage under ₹2,000 with velocity editing and beat sync. "
            "The video should be a 60-second clip delivered in 48 hours, with subtitles."
        )
        self.assertEqual(parsed["game"], "Fortnite")
        self.assertEqual(parsed["budget_max"], 2000)
        self.assertEqual(parsed["duration_seconds"], 60)
        self.assertEqual(parsed["delivery_hours"], 48)
        self.assertIn("velocity editing", parsed["effects"])
        self.assertIn("beat synchronisation", parsed["effects"])
        self.assertEqual(parsed["subtitles"], "Yes")

    def test_form_choice_overrides_description_and_is_reported(self):
        requirements = matching.build_requirements(
            {"game": "Valorant"}, {}, "A Fortnite montage with velocity editing and a budget of ₹1,500."
        )
        self.assertEqual(requirements["values"]["game"], "Valorant")
        self.assertEqual(requirements["sources"]["game"], "Form field")
        self.assertTrue(requirements["conflicts"])

    def test_missing_fields_stay_unspecified(self):
        requirements = matching.build_requirements({}, {}, "Please make me a short video for my channel.")
        self.assertIsNone(requirements["values"]["game"])
        self.assertEqual(requirements["sources"]["game"], "Not specified")


if __name__ == "__main__":
    unittest.main()
