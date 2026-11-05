"""Check release-note preservation and README preview publication safeguards."""

import unittest

from publish_screenshot import (
    END_MARKER, IMAGE_NAME, PREVIEW_BRANCH, RELEASE_PREVIEW_BRANCH, START_MARKER,
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
        if method == "GET" and path in (
            f"git/ref/heads/{PREVIEW_BRANCH}", f"git/ref/heads/{RELEASE_PREVIEW_BRANCH}",
        ):
            return self.reference
        if method == "GET" and path.startswith("git/ref/heads/"):
            return {"object": {"sha": self.source_commits.pop(0)}}
        if method == "GET" and path.startswith("releases/tags/"):
            return self.release
        return {"sha": "generated"}


class ReleaseNotesTests(unittest.TestCase):
    image_url = "https://raw.githubusercontent.com/owner/launcher/" + "a" * 40 + "/" + IMAGE_NAME

    def test_appends_release_specific_image_and_keeps_custom_notes(self):
        notes = "## Changes\n\n- New feature\n\n**Full Changelog**: https://example.com/compare\n"
        updated = update_release_notes(notes, self.image_url)
        self.assertTrue(updated.startswith(notes))
        self.assertIn(self.image_url, updated)
        self.assertNotIn("/releases/download/", updated)
        self.assertEqual(updated.count(START_MARKER), 1)

    def test_rerun_is_idempotent_and_preserves_notes_after_preview(self):
        notes = update_release_notes("Handwritten notes", self.image_url)
        notes += "\nMaintainer addendum.\n"
        self.assertEqual(update_release_notes(notes, self.image_url), notes)

    def test_replaces_only_generated_preview(self):
        notes = f"Before\n{START_MARKER}\nold image\n{END_MARKER}\nAfter"
        updated = update_release_notes(notes, self.image_url)
        self.assertTrue(updated.startswith("Before\n"))
        self.assertTrue(updated.endswith("\nAfter"))
        self.assertNotIn("old image", updated)

    def test_incomplete_markers_are_rejected(self):
        for marker in (START_MARKER, END_MARKER):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                update_release_notes("Notes\n" + marker, self.image_url)

    def test_screenshot_is_embedded_without_a_release_asset(self):
        api = FakeAPI(release={"id": 1, "body": "Notes", "assets": []})
        publish_release_notes(api, "v2.2.0", b"image")
        method, path, payload = api.calls[-1]
        self.assertEqual((method, path), ("PATCH", "releases/1"))
        self.assertTrue(payload["body"].startswith("Notes"))
        self.assertIn("https://raw.githubusercontent.com/owner/launcher/generated/" + IMAGE_NAME, payload["body"])
        self.assertFalse(any("releases/assets/" in path for _, path, _ in api.calls))

    def test_old_attachment_removed_after_notes_update_and_packages_retained(self):
        api = FakeAPI(release={"id": 1, "body": "Notes", "assets": [
            {"id": 2, "name": IMAGE_NAME}, {"id": 3, "name": "launcher.zip"},
            {"id": 4, "name": "launcher-setup.exe"}, {"id": 5, "name": "SHA256SUMS.txt"},
        ]})
        publish_release_notes(api, "v2.2.0", b"image")
        update = next(i for i, (method, path, _) in enumerate(api.calls) if path == "releases/1")
        removal = next(i for i, (method, path, _) in enumerate(api.calls) if method == "DELETE")
        self.assertLess(update, removal)
        self.assertEqual([path for method, path, _ in api.calls if method == "DELETE"], ["releases/assets/2"])

    def test_archived_image_commit_keeps_previous_releases_reachable(self):
        api = FakeAPI(reference={"object": {"sha": "previous-release-preview"}},
                      release={"id": 1, "body": "Notes", "assets": []})
        publish_release_notes(api, "v2.2.0", b"image")
        commit = next(payload for _, path, payload in api.calls if path == "git/commits")
        self.assertEqual(commit["parents"], ["previous-release-preview"])
        branch = next(payload for method, path, payload in api.calls
                      if path == f"git/refs/heads/{RELEASE_PREVIEW_BRANCH}")
        self.assertFalse(branch["force"])

    def test_bad_markers_leave_repository_and_assets_untouched(self):
        api = FakeAPI(release={"id": 1, "body": START_MARKER,
                              "assets": [{"id": 2, "name": IMAGE_NAME}]})
        with self.assertRaises(ValueError):
            publish_release_notes(api, "v2.2.0", b"image")
        self.assertEqual(len(api.calls), 1)


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
