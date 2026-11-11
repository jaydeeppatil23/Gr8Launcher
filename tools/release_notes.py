"""Publish agent-authored change fragments and archive only consumed notes.

Preparing a snapshot is local and read-only apart from its output files. GitHub
writes happen only in the publish/archive commands used by the release workflow.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

if __package__:
    from .build_windows import parse_version
    from .publish_screenshot import GitHubAPI
else:
    from build_windows import parse_version
    from publish_screenshot import GitHubAPI


ROOT = Path(__file__).resolve().parents[1]
PENDING = "docs/changes/unreleased"
ARCHIVE = "docs/changes/releases"
CATEGORIES = ("Added", "Changed", "Fixed", "Removed", "Security")
START = "<!-- gr8-release-notes:start -->"
END = "<!-- gr8-release-notes:end -->"
NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\.md")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def parse_fragment(text: str, name: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.replace("\r\n", "\n").splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            if current not in CATEGORIES:
                raise ValueError(f"{name}: use a supported change category.")
            sections.setdefault(current, [])
        elif line.strip():
            if current is None or (not line.startswith("- ") and not line.startswith("  ")):
                raise ValueError(f"{name}: write bullets under a ### change category.")
            if START in line or END in line:
                raise ValueError(f"{name}: generated release markers are not allowed in fragments.")
            sections[current].append(line.rstrip())
    if not any(any(line.startswith("- ") and line[2:].strip() for line in lines) for lines in sections.values()):
        raise ValueError(f"{name}: the change fragment has no notes.")
    return sections


def committed_fragments(root: Path, commit: str) -> list[tuple[str, bytes]]:
    """Read Git blobs so Windows checkout line endings cannot change hashes."""
    tree = subprocess.check_output(
        ["git", "ls-tree", "-r", "-z", commit, "--", PENDING + "/"], cwd=root,
    )
    fragments = []
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        path = raw_path.decode("utf-8")
        if not path.endswith(".md"):
            continue
        mode, kind, blob = metadata.decode("ascii").split()
        name = path.removeprefix(PENDING + "/")
        if not NAME.fullmatch(name) or mode not in ("100644", "100755") or kind != "blob":
            raise ValueError(f"Invalid committed change fragment: {path}")
        content = subprocess.check_output(["git", "cat-file", "blob", blob], cwd=root)
        fragments.append((path, content))
    return sorted(fragments)


def prepare(root: Path, version: str, commit: str, *, from_commit: bool = False) -> dict:
    version, _ = parse_version(version)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Use the full built commit SHA for the notes snapshot.")
    source = []
    if from_commit:
        source = committed_fragments(root, commit)
    else:
        for path in sorted((root / PENDING).glob("*.md")):
            if not NAME.fullmatch(path.name) or path.is_symlink():
                raise ValueError(f"Invalid change fragment filename: {path.name}")
            source.append((f"{PENDING}/{path.name}", path.read_bytes()))
    fragments = []
    for path, content in source:
        if len(content) > 128 * 1024:
            raise ValueError(f"Change fragment is too large: {path}")
        text = content.decode("utf-8-sig")
        parse_fragment(text, path)
        fragments.append({"path": path, "sha256": digest(content), "text": text})
    if len(fragments) > 200:
        raise ValueError("Too many unreleased change fragments.")
    return {"schema": 1, "version": version, "commit": commit, "fragments": fragments}


def validate_snapshot(snapshot: dict) -> None:
    if not isinstance(snapshot, dict) or snapshot.get("schema") != 1:
        raise ValueError("Unsupported release notes snapshot.")
    if parse_version(snapshot.get("version", ""))[0] != snapshot["version"]:
        raise ValueError("Snapshot version must be normalized.")
    if not re.fullmatch(r"[0-9a-f]{40}", str(snapshot.get("commit", ""))):
        raise ValueError("Invalid snapshot commit.")
    fragments = snapshot.get("fragments")
    if not isinstance(fragments, list) or len(fragments) > 200:
        raise ValueError("Invalid snapshot fragments.")
    names = set()
    for fragment in fragments:
        if not isinstance(fragment, dict):
            raise ValueError("Invalid fragment record.")
        path = fragment.get("path", "")
        if (not isinstance(path, str) or not path.startswith(PENDING + "/")
                or not NAME.fullmatch(path.removeprefix(PENDING + "/")) or path in names):
            raise ValueError("Snapshot contains an unsafe or duplicate fragment path.")
        names.add(path)
        if not re.fullmatch(r"[0-9a-f]{64}", str(fragment.get("sha256", ""))):
            raise ValueError("Invalid fragment digest.")
        if not isinstance(fragment.get("text"), str) or len(fragment["text"]) > 128 * 1024:
            raise ValueError("Invalid fragment text.")
        parse_fragment(fragment["text"], path)


def render(snapshot: dict) -> str:
    validate_snapshot(snapshot)
    grouped: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    for fragment in snapshot["fragments"]:
        for category, lines in parse_fragment(fragment["text"], fragment["path"]).items():
            grouped[category].extend(lines)
    sections = [f"### {category}\n\n" + "\n".join(grouped[category])
                for category in CATEGORIES if grouped[category]]
    if not sections:
        return ""
    return START + "\n## What's new\n\n" + "\n\n".join(sections) + "\n" + END + "\n"


def merge_body(body: str, notes: str) -> str:
    if not notes:
        return body
    if body.count(START) != body.count(END) or body.count(START) > 1:
        raise ValueError("Existing release notes have incomplete or duplicate managed markers.")
    if START in body:
        pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
        if not pattern.search(body):
            raise ValueError("Existing release notes have reversed managed markers.")
        return pattern.sub(lambda _: notes.rstrip(), body)
    return notes.rstrip() + ("\n\n" + body if body else "\n")


def release_record(api: GitHubAPI, snapshot: dict, tag: str) -> dict:
    validate_snapshot(snapshot)
    if tag != "v" + snapshot["version"]:
        raise ValueError("Release tag does not match the notes snapshot.")
    release = api.request("GET", f"releases/tags/{quote(tag, safe='')}")
    if release.get("draft"):
        raise ValueError("Publish the release before publishing or archiving its notes.")
    return release


def publish(api: GitHubAPI, snapshot: dict, tag: str) -> bool:
    notes = render(snapshot)
    if not notes:
        print("No agent-authored notes; keeping GitHub-generated release notes.")
        return False
    release = release_record(api, snapshot, tag)
    body = release.get("body") or ""
    updated = merge_body(body, notes)
    if updated != body:
        api.request("PATCH", f"releases/{release['id']}", {"body": updated})
    print(f"Published agent-authored notes for {tag}.")
    return True


def content_at(api: GitHubAPI, path: str, commit: str) -> bytes | None:
    item = api.request("GET", f"contents/{quote(path, safe='/')}?ref={commit}", allow_missing=True)
    if item is None:
        return None
    if item.get("type") != "file" or item.get("encoding") != "base64":
        raise ValueError(f"Unsupported repository content at {path}.")
    return base64.b64decode(item["content"])


def archive(api: GitHubAPI, snapshot: dict, tag: str, branch: str = "main") -> bool:
    notes = render(snapshot)
    if not notes:
        return False
    release = release_record(api, snapshot, tag)
    body = release.get("body") or ""
    if merge_body(body, notes) != body:
        raise ValueError("Release notes must match the built snapshot before clearing pending fragments.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", branch):
        raise ValueError("Use a simple source branch name.")
    reference = api.request("GET", f"git/ref/heads/{branch}")
    head = reference["object"]["sha"]
    comparison = api.request("GET", f"compare/{snapshot['commit']}...{head}")
    if comparison["status"] not in ("ahead", "identical"):
        print("Keeping pending notes: the source branch no longer contains the built commit.")
        return False
    date = str(release.get("published_at") or "")[:10]
    archived = (f"# Gr8 Launcher v{snapshot['version']}\n\n"
                f"Released: {date}\n\nSource commit: `{snapshot['commit']}`\n\n" + notes)
    archive_path = f"{ARCHIVE}/{snapshot['version']}.md"
    existing = content_at(api, archive_path, head)
    if existing is not None and existing != archived.encode("utf-8"):
        raise ValueError("This version already has a different archive; pending notes were kept.")
    entries = []
    if existing is None:
        entries.append({"path": archive_path, "mode": "100644", "type": "blob", "content": archived})
    for fragment in snapshot["fragments"]:
        current = content_at(api, fragment["path"], head)
        if current is not None and digest(current) == fragment["sha256"]:
            entries.append({"path": fragment["path"], "mode": "100644", "type": "blob", "sha": None})
        elif current is not None:
            print(f"Keeping edited fragment for the next release: {fragment['path']}")
    if not entries:
        print(f"Notes for {tag} are already archived.")
        return False
    commit = api.request("GET", f"git/commits/{head}")
    tree = api.request("POST", "git/trees", {"base_tree": commit["tree"]["sha"], "tree": entries})
    generated = api.request("POST", "git/commits", {
        "message": f"Archive release notes for {tag} [skip ci]", "tree": tree["sha"], "parents": [head],
    })
    # This is a normal fast-forward update. If main moves, GitHub rejects it;
    # no fragment is deleted until the complete tree becomes the branch head.
    api.request("PATCH", f"git/refs/heads/{branch}", {"sha": generated["sha"], "force": False})
    print(f"Archived {tag} and cleared unchanged consumed fragments on {branch}.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_cmd = commands.add_parser("prepare", help="Preview notes and snapshot fragments without publishing")
    prepare_cmd.add_argument("--version", required=True)
    prepare_cmd.add_argument("--commit", help="Read fragments from this commit; otherwise preview working files")
    prepare_cmd.add_argument("--output", type=Path, required=True)
    prepare_cmd.add_argument("--snapshot", type=Path, required=True)
    for name in ("publish", "archive"):
        command = commands.add_parser(name)
        command.add_argument("--snapshot", type=Path, required=True)
        command.add_argument("--tag", required=True)
        if name == "archive":
            command.add_argument("--branch", default="main")
    args = parser.parse_args()
    if args.command == "prepare":
        commit = args.commit or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        snapshot = prepare(ROOT, args.version, commit, from_commit=bool(args.commit))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render(snapshot), encoding="utf-8", newline="\n")
        args.snapshot.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"Prepared {len(snapshot['fragments'])} change fragments for v{snapshot['version']}.")
    else:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        api = GitHubAPI()
        if args.command == "publish":
            publish(api, snapshot, args.tag)
        else:
            archive(api, snapshot, args.tag, args.branch)


if __name__ == "__main__":
    main()
