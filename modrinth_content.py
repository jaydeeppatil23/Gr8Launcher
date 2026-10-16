"""Modrinth content integration for Gr8 Launcher.

Async-friendly wrappers around the synchronous python-modrinth (Pyrinth) client
to browse, download, and track mods, resource packs, and shader packs for a
Minecraft game directory.

Blocking Pyrinth / network calls are run via ``asyncio.to_thread`` so the Flet
UI never freezes.  Content files are placed in the standard Minecraft folders
(``mods/``, ``resourcepacks/``, ``shaderpacks/``) of the selected installation's
game directory, so loaders / the game pick them up automatically -- no launch
command changes are required.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

import pyrinth
from pyrinth.exceptions import InvalidRequestError, NotFoundError

# Category -> target folder inside a Minecraft game directory.
CATEGORY_FOLDERS = {
    "mod": "mods",
    "resourcepack": "resourcepacks",
    "shader": "shaderpacks",
}

# Friendly labels used in the UI.
CATEGORY_LABELS = {
    "mod": "Mods",
    "resourcepack": "Resource Packs",
    "shader": "Shader Packs",
}

# Modrinth project_type values.
VALID_CATEGORIES = tuple(CATEGORY_FOLDERS.keys())

USER_AGENT = "Gr8Launcher/1.0 (https://github.com/jaydeeppatil23/Gr8Launcher)"


class ModrinthError(Exception):
    """Raised when a Modrinth API or download operation fails."""


@dataclass
class ContentFile:
    """A downloadable file from a Modrinth version."""

    name: str
    url: str
    size: int
    hashes: dict[str, str]
    primary_hash: str | None
    primary_hash_algo: str | None
    file_type: str | None
    is_primary: bool


@dataclass
class ContentVersion:
    """A Modrinth version of a content project."""

    version_id: str
    version_number: str
    name: str
    game_versions: list[str]
    loaders: list[str]
    version_type: str
    featured: bool
    files: list[ContentFile]
    changelog: str | None = None


@dataclass
class ContentProject:
    """A Modrinth project (mod / resourcepack / shader)."""

    project_id: str
    slug: str
    title: str
    description: str
    project_type: str
    categories: list[str] = field(default_factory=list)
    client_side: str = ""
    server_side: str = ""
    downloads: int = 0
    icon_url: str | None = None
    versions: list[str] = field(default_factory=list)


@dataclass
class InstalledContent:
    """A tracked piece of installed content."""

    project_id: str
    project_slug: str
    project_title: str
    version_id: str
    version_number: str
    filename: str
    category: str
    enabled: bool = True
    downloaded_at: str | None = None


class ModrinthClient:
    """Async-friendly wrapper around the synchronous Pyrinth client."""

    def __init__(self, user_agent: str = USER_AGENT) -> None:
        self._user_agent = user_agent
        # Pyrinth is synchronous (requests); all public methods here run its
        # blocking calls in an executor thread so the Flet UI stays responsive.
        self._client = pyrinth.Modrinth()

    # ------------------------------------------------------------------ browse

    async def search_projects(
        self,
        project_type: str,
        query: str = "",
        *,
        game_version: str | None = None,
        loader: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ContentProject]:
        """Search Modrinth for projects of a given type.

        ``project_type`` is one of ``mod``, ``resourcepack`` or ``shader``.
        """
        if project_type not in VALID_CATEGORIES:
            raise ModrinthError(f"Unsupported content category: {project_type!r}")

        def _search() -> list[ContentProject]:
            # Note: the Modrinth /search endpoint returns zero results when
            # multiple facet filters are combined (project_type + loader +
            # game_version).  ``project_type`` alone is reliable, so we filter
            # by that here and apply loader / game_version filtering when the
            # user selects a version via ``get_project_versions`` (which uses
            # the more reliable /project/{slug}/version query-params endpoint).
            facets = [["project_type:" + project_type]]
            try:
                results = pyrinth.projects.Project.search(
                    query=query,
                    facets=facets,
                    index="relevance",
                    offset=offset,
                    limit=limit,
                )
            except NotFoundError as exc:
                raise ModrinthError("No projects found.") from exc
            except InvalidRequestError as exc:
                raise ModrinthError(f"Modrinth search error: {exc}") from exc
            except Exception as exc:  # network, etc.
                raise ModrinthError(f"Failed to contact Modrinth: {exc}") from exc

            projects_list: list[ContentProject] = []
            for result in results:
                model = getattr(result, "model", None) or result
                projects_list.append(
                    ContentProject(
                        project_id=getattr(model, "project_id", None)
                        or getattr(model, "id", None)
                        or getattr(result, "project_id", None)
                        or getattr(result, "id", None)
                        or "",
                        slug=getattr(model, "slug", None)
                        or getattr(result, "slug", None)
                        or "",
                        title=getattr(model, "title", None) or "",
                        description=getattr(model, "description", None) or "",
                        project_type=getattr(model, "project_type", None) or project_type,
                        categories=list(getattr(model, "categories", None) or []),
                        client_side=getattr(model, "client_side", None) or "",
                        server_side=getattr(model, "server_side", None) or "",
                        downloads=int(getattr(model, "downloads", None) or 0),
                        icon_url=getattr(model, "icon_url", None),
                        versions=list(getattr(model, "versions", None) or []),
                    )
                )
            return projects_list

        return await asyncio.to_thread(_search)

    async def get_project(self, project_id: str) -> ContentProject:
        """Fetch a single project by ID or slug."""

        def _get() -> ContentProject:
            try:
                project = pyrinth.projects.Project.get(project_id)
            except NotFoundError as exc:
                raise ModrinthError(f"Project not found: {project_id}") from exc
            except InvalidRequestError as exc:
                raise ModrinthError(f"Modrinth error: {exc}") from exc
            except Exception as exc:
                raise ModrinthError(f"Failed to fetch project: {exc}") from exc

            model = project.model
            return ContentProject(
                project_id=getattr(model, "id", "") or "",
                slug=getattr(model, "slug", "") or "",
                title=getattr(model, "title", "") or "",
                description=getattr(model, "description", "") or "",
                project_type=getattr(model, "project_type", "") or "",
                categories=list(getattr(model, "categories", []) or []),
                client_side=getattr(model, "client_side", "") or "",
                server_side=getattr(model, "server_side", "") or "",
                downloads=int(getattr(model, "downloads", 0) or 0),
                icon_url=getattr(model, "icon_url", None),
                versions=list(getattr(model, "version_ids", []) or []),
            )

        return await asyncio.to_thread(_get)

    async def get_project_versions(
        self,
        project_id: str,
        *,
        game_version: str | None = None,
        loader: str | None = None,
        limit: int = 25,
    ) -> list[ContentVersion]:
        """List versions of a project, optionally filtered by MC version/loader."""

        def _get() -> list[ContentVersion]:
            try:
                project = pyrinth.projects.Project.get(project_id)
                loaders = [loader] if loader else None
                game_versions = [game_version] if game_version else None
                versions = project.get_versions(
                    loaders=loaders,
                    game_versions=game_versions,
                )
            except NotFoundError as exc:
                raise ModrinthError(f"Project not found: {project_id}") from exc
            except InvalidRequestError as exc:
                raise ModrinthError(f"Modrinth error: {exc}") from exc
            except Exception as exc:
                raise ModrinthError(f"Failed to fetch versions: {exc}") from exc

            results: list[ContentVersion] = []
            for version in versions[:limit]:
                model = version.model
                files: list[ContentFile] = []
                for file in version.get_files():
                    hashes = file.hashes or {}
                    # Pyrinth exposes ``file.primary`` as a boolean (primary-file
                    # flag), not a hash algorithm.  Pick an available hash
                    # algorithm (prefer sha1) for integrity verification.
                    primary_algo = None
                    for algo in ("sha1", "sha512"):
                        if algo in hashes:
                            primary_algo = algo
                            break
                    if not primary_algo and hashes:
                        primary_algo = next(iter(hashes))
                    primary_hash = hashes.get(primary_algo) if primary_algo else None
                    files.append(
                        ContentFile(
                            name=file.name or "",
                            url=file.url or "",
                            size=int(file.size or 0),
                            hashes=dict(hashes),
                            primary_hash=primary_hash,
                            primary_hash_algo=primary_algo,
                            file_type=file.file_type or None,
                            is_primary=bool(file.primary),
                        )
                    )
                results.append(
                    ContentVersion(
                        version_id=getattr(model, "id", "") or "",
                        version_number=getattr(model, "version_number", "") or "",
                        name=getattr(model, "name", "") or "",
                        game_versions=list(getattr(model, "game_versions", []) or []),
                        loaders=list(getattr(model, "loaders", []) or []),
                        version_type=getattr(model, "version_type", "") or "",
                        featured=bool(getattr(model, "featured", False)),
                        files=files,
                        changelog=getattr(model, "changelog", None),
                    )
                )
            return results

        return await asyncio.to_thread(_get)

    # ------------------------------------------------------------------- download

    async def download_file(
        self,
        url: str,
        dest: Path,
        *,
        progress_cb: Callable[[int, int], None] | None = None,
        expected_hash: str | None = None,
        expected_hash_algo: str | None = None,
        timeout: int = 60,
    ) -> None:
        """Download ``url`` to ``dest`` with progress reporting and hash check."""

        def _download() -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = dest.with_suffix(dest.suffix + ".part")
            try:
                with requests.get(
                    url,
                    stream=True,
                    timeout=timeout,
                    headers={"User-Agent": self._user_agent},
                ) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length", 0) or 0)
                    downloaded = 0
                    algo = (expected_hash_algo or "sha1").lower()
                    digest = hashlib.new(algo) if expected_hash else None
                    with tmp_path.open("wb") as handle:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            handle.write(chunk)
                            downloaded += len(chunk)
                            if digest is not None:
                                digest.update(chunk)
                            if progress_cb is not None:
                                progress_cb(downloaded, total if total > 0 else 0)
                if digest is not None:
                    actual = digest.hexdigest().lower()
                    if actual != (expected_hash or "").lower():
                        raise ModrinthError(
                            f"{algo.upper()} mismatch: downloaded file may be corrupted."
                        )
                tmp_path.replace(dest)
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

        await asyncio.to_thread(_download)

    # ------------------------------------------------------------------- folders

    @staticmethod
    def content_folder_for_category(game_dir: str | Path, category: str) -> Path:
        """Return the target folder for a content category (creating it if needed)."""
        if category not in CATEGORY_FOLDERS:
            raise ModrinthError(f"Unsupported content category: {category!r}")
        folder = Path(game_dir) / CATEGORY_FOLDERS[category]
        folder.mkdir(parents=True, exist_ok=True)
        return folder
