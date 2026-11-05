"""Check release-note preservation and README preview publication safeguards."""

import unittest

from publish_screenshot import (
    END_MARKER, IMAGE_NAME, PREVIEW_BRANCH, START_MARKER,
    publish_preview, publish_release_notes, update_release_notes,
)


class FakeAPI:
    repository = "owner/launcher"

    def __init__(self, source_commits=None, reference=None, release=None):
        self.source_commits = list(source_commits or ["a" * 40, "a" * 40])
        self.reference = reference
        self.release = release
        self.calls = []

    def request(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, payload))
        if method == "GET" and path == f"git/ref/heads/{PREVIEW_BRANCH}":
            return self.reference
        if method == "GET" and path.startswith("git/ref/heads/"):
            return {"object": {"sha": self.source_commits.pop(0)}}
        if method == "GET" and path.startswith("releases/tags/"):
            return self.release
        return {"sha": "generated"}


class ReleaseNotesTests(unittest.TestCase):
    def test_appends_release_specific_image_and_keeps_custom_notes(self):
        notes = "## Changes\n\n- New feature\n\n**Full Changelog**: https://example.com/compare\n"
        updated = update_release_notes(notes, "owner/launcher", "v2.2.0-beta.1")
        self.assertTrue(updated.startswith(notes))
        self.assertIn("https://github.com/owner/launcher/releases/download/v2.2.0-beta.1/" + IMAGE_NAME, updated)
        self.assertEqual(updated.count(START_MARKER), 1)

    def test_rerun_is_idempotent_and_preserves_notes_after_preview(self):
        notes = update_release_notes("Handwritten notes", "owner/launcher", "v2.2.0")
        notes += "\nMaintainer addendum.\n"
        self.assertEqual(update_release_notes(notes, "owner/launcher", "v2.2.0"), notes)

    def test_replaces_only_generated_preview(self):
        notes = f"Before\n{START_MARKER}\nold image\n{END_MARKER}\nAfter"
        updated = update_release_notes(notes, "owner/launcher", "v2.2.0")
        self.assertTrue(updated.startswith("Before\n"))
        self.assertTrue(updated.endswith("\nAfter"))
        self.assertNotIn("old image", updated)

    def test_incomplete_markers_are_rejected(self):
        for marker in (START_MARKER, END_MARKER):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                update_release_notes("Notes\n" + marker, "owner/launcher", "v2.2.0")

    def test_missing_asset_does_not_change_release(self):
        api = FakeAPI(release={"id": 1, "body": "Notes", "assets": []})
        with self.assertRaisesRegex(ValueError, "Upload"):
            publish_release_notes(api, "v2.2.0")
        self.assertFalse(any(method == "PATCH" for method, _, _ in api.calls))

    def test_uploaded_image_is_embedded_and_existing_notes_survive(self):
        api = FakeAPI(release={"id": 1, "body": "Notes", "assets": [{"name": IMAGE_NAME}]})
        publish_release_notes(api, "v2.2.0")
        method, path, payload = api.calls[-1]
        self.assertEqual((method, path), ("PATCH", "releases/1"))
        self.assertTrue(payload["body"].startswith("Notes"))


class PreviewPublicationTests(unittest.TestCase):
    def test_first_preview_creates_an_image_branch_without_app_source(self):
        api = FakeAPI()
        self.assertTrue(publish_preview(api, b"image", "a" * 40, "main", "2.2.0"))
        tree = next(payload for method, path, payload in api.calls if path == "git/trees")
        self.assertEqual({entry["path"] for entry in tree["tree"]}, {IMAGE_NAME, "source.json", "README.md"})
        commit = next(payload for method, path, payload in api.calls if path == "git/commits")
        self.assertEqual(commit["parents"], [])
        self.assertEqual(api.calls[-1][2]["ref"], f"refs/heads/{PREVIEW_BRANCH}")

    def test_existing_preview_advances_without_force_push(self):
        api = FakeAPI(reference={"object": {"sha": "old-preview"}})
        publish_preview(api, b"image", "a" * 40, "main", "2.2.0")
        commit = next(payload for method, path, payload in api.calls if path == "git/commits")
        self.assertEqual(commit["parents"], ["old-preview"])
        self.assertEqual(api.calls[-1], ("PATCH", f"git/refs/heads/{PREVIEW_BRANCH}",
                                      {"sha": "generated", "force": False}))

    def test_old_build_never_updates_preview(self):
        api = FakeAPI(source_commits=["b" * 40])
        self.assertFalse(publish_preview(api, b"image", "a" * 40, "main", "2.2.0"))
        self.assertEqual(len(api.calls), 1)

    def test_source_change_during_publication_does_not_move_image_branch(self):
        api = FakeAPI(source_commits=["a" * 40, "b" * 40])
        self.assertFalse(publish_preview(api, b"image", "a" * 40, "main", "2.2.0"))
        self.assertFalse(any(path.startswith("git/refs") for _, path, _ in api.calls))


if __name__ == "__main__":
    unittest.main()
