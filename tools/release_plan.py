"""Choose the package version and whether a GitHub Actions run publishes it."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess

if __package__:
    from .build_windows import parse_version, read_project_version
else:
    from build_windows import parse_version, read_project_version


@dataclass(frozen=True)
class ReleasePlan:
    version: str
    tag: str
    publish: bool


def version_numbers(version: str) -> tuple[int, ...]:
    _, numeric = parse_version(version)
    return tuple(int(part) for part in numeric.split("."))


def plan_release(version: str, tags: dict[str, str], commit: str) -> ReleasePlan:
    version, _ = parse_version(version)
    tag = f"v{version}"
    if tag in tags:
        if tags[tag] == commit:
            # A rerun repairs this release's uploads using the same source.
            return ReleasePlan(version, tag, True)
        # Subsequent commits with the same VERSION are preview builds.
        preview = f"{version}.dev" if "-" in version else f"{version}-dev"
        return ReleasePlan(preview, tag, False)

    stable_versions = []
    for existing_tag in tags:
        if not existing_tag.startswith("v"):
            continue
        try:
            existing, _ = parse_version(existing_tag)
        except ValueError:
            continue
        if "-" not in existing:
            stable_versions.append(existing)
    if stable_versions:
        latest = max(stable_versions, key=version_numbers)
        if version_numbers(version) <= version_numbers(latest):
            raise ValueError(f"New version {version} must be newer than v{latest}.")
    return ReleasePlan(version, tag, True)


def git_output(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], text=True).strip()


def load_tags() -> dict[str, str]:
    tags = {}
    for name in git_output("tag", "--list", "v*").splitlines():
        try:
            parse_version(name)
        except ValueError:
            continue
        # Resolve annotated tags to commits as well as lightweight tags.
        tags[name] = git_output("rev-parse", f"refs/tags/{name}^{{commit}}")
    return tags


def choose_plan(event: str, ref: str, version: str, preview: str,
                tags: dict[str, str], commit: str) -> ReleasePlan:
    if event == "push" and ref == "refs/heads/main":
        return plan_release(version, tags, commit)
    if event == "push" and ref.startswith("refs/tags/v"):
        tag = ref.removeprefix("refs/tags/")
        requested, _ = parse_version(tag)
        if tags.get(tag) != commit:
            raise ValueError(f"Tag {tag} must point to the commit being built.")
        return ReleasePlan(requested, tag, True)
    requested, _ = parse_version(preview)
    return ReleasePlan(requested, f"v{requested}", False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without writing workflow outputs")
    args = parser.parse_args()
    try:
        commit = git_output("rev-parse", "HEAD")
        plan = choose_plan(
            os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch"),
            os.environ.get("GITHUB_REF", "refs/heads/main"),
            read_project_version(),
            os.environ.get("PREVIEW_VERSION", "0.0.0-dev"),
            load_tags(), commit,
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Release version check failed: {error}\n")

    outputs = {
        "version": plan.version, "tag": plan.tag,
        "publish": str(plan.publish).lower(), "commit": commit,
    }
    print(json.dumps(outputs, indent=2))
    if not args.dry_run:
        if os.environ.get("GITHUB_ENV"):
            with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8", newline="\n") as output:
                output.write(f"BUILD_VERSION={plan.version}\n")
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8", newline="\n") as output:
                output.write("".join(f"{key}={value}\n" for key, value in outputs.items()))


if __name__ == "__main__":
    main()
