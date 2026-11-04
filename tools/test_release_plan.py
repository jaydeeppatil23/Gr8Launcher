"""Verify version selection before allowing the workflow to publish a release."""

from pathlib import Path
import tempfile
import unittest

from build_windows import parse_version, read_project_version
from release_plan import choose_plan, plan_release


class ReleasePlanTests(unittest.TestCase):
    def setUp(self):
        self.tags = {"v1.2.0": "older", "v2.0.0": "released"}

    def test_next_patch_is_published(self):
        plan = plan_release("2.0.1", self.tags, "new")
        self.assertEqual((plan.version, plan.tag, plan.publish), ("2.0.1", "v2.0.1", True))

    def test_exact_larger_version_is_allowed(self):
        plan = plan_release("2.2.0", self.tags, "new")
        self.assertEqual((plan.version, plan.tag, plan.publish), ("2.2.0", "v2.2.0", True))

    def test_unchanged_released_version_only_builds_preview(self):
        plan = plan_release("2.0.0", self.tags, "new")
        self.assertEqual((plan.version, plan.publish), ("2.0.0-dev", False))

    def test_rerun_reuses_version_and_commit(self):
        plan = plan_release("2.0.0", self.tags, "released")
        self.assertEqual((plan.version, plan.publish), ("2.0.0", True))

    def test_prerelease_is_published(self):
        plan = plan_release("2.1.0-beta.1", self.tags, "new")
        self.assertEqual((plan.version, plan.tag, plan.publish), ("2.1.0-beta.1", "v2.1.0-beta.1", True))

    def test_unchanged_prerelease_has_valid_preview_version(self):
        self.tags["v2.1.0-beta.1"] = "released"
        plan = plan_release("2.1.0-beta.1", self.tags, "new")
        self.assertFalse(plan.publish)
        self.assertEqual(parse_version(plan.version)[0], "2.1.0-beta.1.dev")

    def test_unreleased_downgrade_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "newer than v2.0.0"):
            plan_release("1.9.0", self.tags, "new")

    def test_sorting_uses_numeric_version_components(self):
        self.tags.update({"v2.0.9": "old", "v2.0.10": "latest"})
        with self.assertRaises(ValueError):
            plan_release("2.0.8", self.tags, "new")
        self.assertTrue(plan_release("2.0.11", self.tags, "new").publish)

    def test_unrelated_tags_are_ignored(self):
        self.tags.update({"vpreview": "other", "v99999.0.0": "invalid"})
        self.assertTrue(plan_release("2.0.1", self.tags, "new").publish)

    def test_invalid_versions_are_rejected(self):
        for version in ("v2", "2.0.65536", "2.0.1\nBAD=1", "2.0.1-beta.01", "2.0.1/unsafe"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                plan_release(version, self.tags, "new")

    def test_pull_request_never_publishes(self):
        plan = choose_plan("pull_request", "refs/pull/1/merge", "2.2.0", "0.0.0-dev", self.tags, "new")
        self.assertFalse(plan.publish)

    def test_manual_preview_never_publishes(self):
        plan = choose_plan("workflow_dispatch", "refs/heads/main", "2.2.0", "2.2.0-beta.1", self.tags, "new")
        self.assertEqual((plan.version, plan.publish), ("2.2.0-beta.1", False))

    def test_main_push_uses_requested_version(self):
        plan = choose_plan("push", "refs/heads/main", "2.2.0", "0.0.0-dev", self.tags, "new")
        self.assertEqual((plan.version, plan.publish), ("2.2.0", True))

    def test_existing_tag_push_is_supported(self):
        plan = choose_plan("push", "refs/tags/v2.0.0", "2.2.0", "0.0.0-dev", self.tags, "released")
        self.assertEqual((plan.version, plan.publish), ("2.0.0", True))

    def test_tag_moved_to_another_commit_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "commit being built"):
            choose_plan("push", "refs/tags/v2.0.0", "2.2.0", "0.0.0-dev", self.tags, "different")

    def test_version_file_handles_powershell_utf8_bom(self):
        with tempfile.TemporaryDirectory(prefix="gr8-version-check-") as directory:
            version_file = Path(directory) / "VERSION"
            version_file.write_text("2.2.0\n", encoding="utf-8-sig")
            self.assertEqual(read_project_version(version_file), "2.2.0")


if __name__ == "__main__":
    unittest.main()
