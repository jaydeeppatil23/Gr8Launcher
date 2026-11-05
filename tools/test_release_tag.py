"""Prevent missing tags and tag objects from causing false release conflicts."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from check_release_tag import check_release_tag, main, resolve_remote_tag


class ReleaseTagTests(unittest.TestCase):
    commit = "3bae5e87a1b3c0c61bb5e3d06ee116a2dbf44aaf"

    def test_new_release_with_missing_tag_is_allowed(self):
        with patch("check_release_tag.subprocess.check_output", return_value="") as git:
            self.assertIsNone(check_release_tag("v2.1.1", self.commit))
        git.assert_called_once_with([
            "git", "ls-remote", "--tags", "origin", "refs/tags/v2.1.1", "refs/tags/v2.1.1^{}",
        ], text=True)

    def test_lightweight_tag_at_built_commit_is_allowed(self):
        with patch("check_release_tag.subprocess.check_output",
                   return_value=f"{self.commit}\trefs/tags/v2.1.1\n"):
            self.assertEqual(check_release_tag("v2.1.1", self.commit), self.commit)

    def test_annotated_tag_compares_peeled_commit(self):
        output = f"{'a' * 40}\trefs/tags/v2.1.1\n{self.commit}\trefs/tags/v2.1.1^{{}}\n"
        with patch("check_release_tag.subprocess.check_output", return_value=output):
            self.assertEqual(check_release_tag("v2.1.1", self.commit), self.commit)

    def test_genuine_tag_conflict_is_rejected_with_both_commits(self):
        with patch("check_release_tag.subprocess.check_output",
                   return_value=f"{'b' * 40}\trefs/tags/v2.1.1\n"):
            with self.assertRaises(ValueError) as error:
                check_release_tag("v2.1.1", self.commit)
        self.assertIn("b" * 40, str(error.exception))
        self.assertIn(self.commit, str(error.exception))

    def test_network_failure_is_not_treated_as_missing_tag(self):
        with patch("check_release_tag.subprocess.check_output",
                   side_effect=subprocess.CalledProcessError(128, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                check_release_tag("v2.1.1", self.commit)

    def test_similarly_named_tag_does_not_match(self):
        output = f"{'b' * 40}\trefs/tags/v2.1.10\n"
        self.assertIsNone(resolve_remote_tag(output, "v2.1.1"))

    def test_non_sha_output_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_remote_tag("null\trefs/tags/v2.1.1\n", "v2.1.1")

    def test_invalid_inputs_never_contact_remote(self):
        for tag, commit in (("2.1.1", self.commit), ("v2.1.1\nBAD=1", self.commit), ("v2.1.1", "null")):
            with self.subTest(tag=tag, commit=commit):
                with patch("check_release_tag.subprocess.check_output") as git:
                    with self.assertRaises(ValueError):
                        check_release_tag(tag, commit)
                    git.assert_not_called()

    def test_missing_tag_writes_empty_workflow_output(self):
        with tempfile.TemporaryDirectory(prefix="gr8-tag-check-") as directory:
            output = Path(directory) / "output.txt"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
                 patch.object(sys, "argv", ["check_release_tag.py", "--tag", "v2.1.1", "--commit", self.commit]), \
                 patch("check_release_tag.subprocess.check_output", return_value=""):
                main()
            self.assertEqual(output.read_text(encoding="utf-8"), "existing_sha=\n")


if __name__ == "__main__":
    unittest.main()
