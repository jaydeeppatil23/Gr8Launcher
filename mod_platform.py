"""Provider routing with stable Modrinth IDs and separate CurseForge IDs."""

from __future__ import annotations

import json
from pathlib import Path

from curseforge_services import CurseForgeClient
from mod_services import InstallPlan, ModrinthClient, primary_file


class ModPlatformClient(ModrinthClient):
    def __init__(self, *, curseforge: CurseForgeClient | None = None):
        super().__init__()
        self.curseforge = curseforge or CurseForgeClient()

    @staticmethod
    def _is_curseforge(identifier: str) -> bool:
        return isinstance(identifier, str) and identifier.startswith("cf:")

    def clear_cache(self) -> None:
        super().clear_cache()
        self.curseforge.clear_cache()

    def search(self, query: str, minecraft_version: str, loader: str, *, provider: str = "modrinth", **kwargs) -> dict:
        if provider == "curseforge":
            return self.curseforge.search(query, minecraft_version, loader, **kwargs)
        if provider != "modrinth":
            raise ValueError("Choose Modrinth or CurseForge.")
        return super().search(query, minecraft_version, loader, **kwargs)

    def project(self, project_id: str) -> dict:
        return self.curseforge.project(project_id) if self._is_curseforge(project_id) else super().project(project_id)

    def project_members(self, project_id: str) -> list[dict]:
        return self.curseforge.project_members(project_id) if self._is_curseforge(project_id) else super().project_members(project_id)

    def project_versions(self, project_id: str, minecraft_version: str | None = None, loader: str | None = None) -> list[dict]:
        if self._is_curseforge(project_id):
            return self.curseforge.project_versions(project_id, minecraft_version, loader)
        return super().project_versions(project_id, minecraft_version, loader)

    def compatible_version(self, project_id: str, minecraft_version: str, loader: str) -> dict:
        if self._is_curseforge(project_id):
            return self.curseforge.compatible_version(project_id, minecraft_version, loader)
        return super().compatible_version(project_id, minecraft_version, loader)

    def version(self, version_id: str) -> dict:
        return self.curseforge.version(version_id) if self._is_curseforge(version_id) else super().version(version_id)

    def fabric_loader_metadata(self, version: dict) -> list[dict]:
        if self._is_curseforge(version.get("project_id", "")):
            return self.curseforge.fabric_loader_metadata(version)
        return super().fabric_loader_metadata(version)

    def download(self, file: dict, destination: Path, progress) -> None:
        if file.get("provider") == "curseforge":
            self.curseforge.download(file, destination, progress)
        else:
            super().download(file, destination, progress)

    def identify_files(self, files: dict[str, Path]) -> tuple[dict[str, dict], tuple[str, ...]]:
        identified = {}
        warnings = []
        try:
            identified.update(self.versions_for_files(list(files)))
        except (ConnectionError, ValueError):
            warnings.append("Modrinth identification is unavailable. Local and cached identities remain available.")
        remaining = {digest: path for digest, path in files.items() if digest not in identified}
        if remaining and self.curseforge.configured:
            try:
                identified.update(self.curseforge.identify_files(remaining))
            except (ConnectionError, ValueError):
                warnings.append("CurseForge identification is unavailable. Local and Modrinth actions remain available.")
        return identified, tuple(warnings)

    def identify_local_file(self, path: Path, digest: str) -> dict | None:
        identified, _ = self.identify_files({digest: path})
        return identified.get(digest)

    def manual_files_ready(self, plan: InstallPlan) -> bool:
        return all(self.curseforge.manual_file(file) is not None for version in plan.versions
                   if (file := primary_file(version)).get("manual_required"))

    def is_manual_file_ready(self, file: dict) -> bool:
        return self.curseforge.manual_file(file) is not None

    def register_manual_files(self, plan: InstallPlan, paths: list[Path]) -> int:
        return self.curseforge.register_manual_files([primary_file(version) for version in plan.versions], paths)

    def search_modpacks(self, query: str, *, provider: str = "modrinth", index: str = "downloads",
                        offset: int = 0, limit: int = 20) -> dict:
        if provider == "curseforge":
            return self.curseforge.search_modpacks(query, index=index, offset=offset, limit=limit)
        if provider != "modrinth":
            raise ValueError("Choose Modrinth or CurseForge.")
        data = self._get("/search", {"query": query, "index": index, "offset": offset, "limit": limit,
                                   "facets": json.dumps([["project_type:modpack"],
                                                         ["client_side:required", "client_side:optional"]])})
        if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
            raise ValueError("Modrinth returned an invalid modpack search result.")
        if any(not isinstance(hit, dict) or hit.get("project_type") != "modpack" or not hit.get("project_id")
               for hit in data["hits"]):
            raise ValueError("Modrinth returned a project that is not a modpack.")
        return data

    def modpack_project(self, project_id: str) -> dict:
        if self._is_curseforge(project_id):
            return self.curseforge.modpack_project(project_id)
        project = super().project(project_id)
        if project.get("id") != project_id or project.get("project_type") != "modpack":
            raise ValueError("This Modrinth project is not the requested modpack.")
        return {**project, "title": project.get("title") or project.get("name", "Modpack"),
                "project_id": project_id, "provider": "modrinth",
                "project_url": "https://modrinth.com/modpack/" + str(project.get("slug") or project_id)}

    def modpack_versions(self, project_id: str) -> list[dict]:
        if self._is_curseforge(project_id):
            return self.curseforge.modpack_versions(project_id)
        self.modpack_project(project_id)
        versions = super().project_versions(project_id)
        if any(version.get("project_id") != project_id for version in versions):
            raise ValueError("Modrinth returned a different modpack's versions.")
        return [version for version in versions
                if any(str(file.get("filename", "")).lower().endswith(".mrpack") for file in version.get("files", []))]

    def modpack_version(self, version_id: str) -> dict:
        if self._is_curseforge(version_id):
            return self.curseforge.modpack_version(version_id)
        version = super().version(version_id)
        if version.get("id") != version_id:
            raise ValueError("Modrinth returned a different modpack version.")
        self.modpack_project(version["project_id"])
        return version
