"""Verify the remote release tag before uploading packages to GitHub."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess

if __package__:
    from .build_windows import parse_version
else:
    from build_windows import parse_version


def validate_inputs(tag: str, commit: str) -> None:
    version, _ = parse_version(tag)
    if tag != f"v{version}":
        raise ValueError("The release tag must start with v.")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("The built commit must be a full commit SHA.")


def resolve_remote_tag(output: str, tag: str) -> str | None:
    reference = f"refs/tags/{tag}"
    matches = {}
    for line in output.splitlines():
        object_id, name = line.split()
        if name in (reference, reference + "^{}"):
            if not re.fullmatch(r"[0-9a-f]{40}", object_id):
                raise ValueError("Git returned an invalid tag SHA.")
            matches[name] = object_id
    # Annotated tags point to a tag object; use its peeled commit for comparison.
    return matches.get(reference + "^{}", matches.get(reference))


def check_release_tag(tag: str, commit: str) -> str | None:
    validate_inputs(tag, commit)
    # A missing tag is a successful, empty result. Authentication and network
    # failures remain errors instead of becoming a fabricated conflicting SHA.
    output = subprocess.check_output([
        "git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}",
    ], text=True)
    existing = resolve_remote_tag(output, tag)
    if existing is not None and existing != commit:
        raise ValueError(f"Tag {tag} points to {existing}, but this build is {commit}. "
                         "Choose a new version in VERSION.")
    return existing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=os.environ.get("RELEASE_TAG", ""))
    parser.add_argument("--commit", default=os.environ.get("RELEASE_SHA", ""))
    args = parser.parse_args()
    try:
        existing = check_release_tag(args.tag, args.commit)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release tag check failed: {error}\n")
    if existing:
        print(f"Tag {args.tag} matches the built commit {existing}.")
    else:
        print(f"Tag {args.tag} does not exist; the release will create it at {args.commit}.")
    if output_file := os.environ.get("GITHUB_OUTPUT"):
        with Path(output_file).open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"existing_sha={existing or ''}\n")


if __name__ == "__main__":
    main()
