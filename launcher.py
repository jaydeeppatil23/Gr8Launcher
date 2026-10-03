import asyncio
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import minecraft_launcher_lib
from storage import load_pending_installations, save_pending_installations


minecraft_directory = minecraft_launcher_lib.utils.get_minecraft_directory()


class DownloadCancelled(Exception):
	pass


def get_system_memory_mb() -> int:
	try:
		if os.name == "nt":
			class MemoryStatus(ctypes.Structure):
				_fields_ = [
					("dwLength", ctypes.c_ulong),
					("dwMemoryLoad", ctypes.c_ulong),
					("ullTotalPhys", ctypes.c_ulonglong),
					("ullAvailPhys", ctypes.c_ulonglong),
					("ullTotalPageFile", ctypes.c_ulonglong),
					("ullAvailPageFile", ctypes.c_ulonglong),
					("ullTotalVirtual", ctypes.c_ulonglong),
					("ullAvailVirtual", ctypes.c_ulonglong),
					("ullAvailExtendedVirtual", ctypes.c_ulonglong),
				]

			status = MemoryStatus()
			status.dwLength = ctypes.sizeof(status)
			if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
				return max(256, status.ullTotalPhys // (1024 * 1024))
		page_size = os.sysconf("SC_PAGE_SIZE")
		page_count = os.sysconf("SC_PHYS_PAGES")
		return max(256, page_size * page_count // (1024 * 1024))
	except (AttributeError, OSError, TypeError, ValueError):
		return 8192


def remove_installed_version(version_id: str) -> bool:
	if not version_id or Path(version_id).name != version_id:
		return False

	version_root = Path(minecraft_directory) / "versions"
	version_path = version_root / version_id
	loose_version_files = (
		version_root / f"{version_id}.json",
		version_root / f"{version_id}.jar",
	)
	try:
		if version_path.resolve().parent != version_root.resolve():
			return False
		if any(path.resolve().parent != version_root.resolve() for path in loose_version_files):
			return False
	except OSError:
		return False

	profile_updates = []
	for filename in ("launcher_profiles.json", "launch_profiles.json"):
		profiles_path = Path(minecraft_directory) / filename
		if not profiles_path.exists():
			continue
		try:
			document = json.loads(profiles_path.read_text(encoding="utf-8"))
			if not isinstance(document, dict):
				return False
			profiles = document.get("profiles", {})
			if not isinstance(profiles, dict):
				return False
			updated_profiles = {
				profile_id: profile
				for profile_id, profile in profiles.items()
				if not isinstance(profile, dict)
				or profile.get("lastVersionId") != version_id
			}
			if len(updated_profiles) != len(profiles):
				document["profiles"] = updated_profiles
				profile_updates.append((profiles_path, document))
		except (OSError, UnicodeError, json.JSONDecodeError):
			return False

	temporary_files = []
	try:
		for profiles_path, document in profile_updates:
			temporary_file = profiles_path.with_suffix(profiles_path.suffix + ".tmp")
			temporary_file.write_text(json.dumps(document, indent=2), encoding="utf-8")
			temporary_files.append((temporary_file, profiles_path))
		for temporary_file, profiles_path in temporary_files:
			temporary_file.replace(profiles_path)
		if version_path.exists():
			shutil.rmtree(version_path)
		for version_file in loose_version_files:
			version_file.unlink(missing_ok=True)
		if version_path.exists() or any(path.exists() for path in loose_version_files):
			return False
	except (OSError, TypeError, ValueError):
		for temporary_file, _ in temporary_files:
			try:
				temporary_file.unlink(missing_ok=True)
			except OSError:
				pass
		return False
	invalidate_installed_versions_cache()
	return True


def generate_offline_uuid(player_name: str) -> str:
	digest = bytearray(hashlib.md5(f"OfflinePlayer:{player_name}".encode("utf-8")).digest())
	return str(uuid.UUID(bytes=bytes(digest), version=3))


def get_local_version_catalog() -> tuple[list[dict], str | None]:
	try:
		versions = minecraft_launcher_lib.utils.get_installed_versions(
			minecraft_directory
		)
	except (OSError, ValueError):
		return [], None

	releases = [version for version in versions if version.get("type") == "release"]
	latest = max(releases, key=lambda version: version["releaseTime"], default=None)
	return versions, latest["id"] if latest else None


def get_version_catalog() -> tuple[list[dict], str | None, bool]:
	try:
		versions = minecraft_launcher_lib.utils.get_version_list()
		latest_release = minecraft_launcher_lib.utils.get_latest_version()["release"]
		return versions, latest_release, True
	except Exception:
		versions, latest_release = get_local_version_catalog()
		return versions, latest_release, False


_installed_versions_cache: list[dict[str, str]] | None = None


def invalidate_installed_versions_cache():
	global _installed_versions_cache
	_installed_versions_cache = None


def get_installed_versions(force_refresh: bool = False) -> list[dict[str, str]]:
	global _installed_versions_cache
	if not force_refresh and _installed_versions_cache is not None:
		return list(_installed_versions_cache)

	try:
		versions = minecraft_launcher_lib.utils.get_installed_versions(
			minecraft_directory
		)
	except (OSError, ValueError):
		return []

	version_root = Path(minecraft_directory) / "versions"
	pending_versions = load_pending_installations()
	records = []
	for version in versions:
		version_id = version["id"]
		if version_id in pending_versions:
			continue
		metadata_path = version_root / version_id / f"{version_id}.json"
		try:
			metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
		except (OSError, UnicodeError, json.JSONDecodeError):
			metadata = {}

		minecraft_version = metadata.get("inheritsFrom", version_id)
		main_class = metadata.get("mainClass", "").lower()
		lowered_id = version_id.lower()
		loader = "Vanilla"
		loader_version = ""
		if "fabric" in main_class or lowered_id.startswith("fabric-loader-"):
			loader = "Fabric"
			loader_version = _loader_version_from_id(
				version_id, "fabric-loader-", minecraft_version
			)
		elif "quilt" in main_class or lowered_id.startswith("quilt-loader-"):
			loader = "Quilt"
			loader_version = _loader_version_from_id(
				version_id, "quilt-loader-", minecraft_version
			)
		elif "neoforge" in main_class or lowered_id.startswith("neoforge-"):
			loader = "NeoForge"
			loader_version = _loader_version_from_id(
				version_id, "neoforge-", minecraft_version
			)
		elif "forge" in main_class or "-forge-" in lowered_id:
			loader = "Forge"
			loader_version = version_id.rsplit("-forge-", 1)[-1]

		records.append(
			{
				"version_id": version_id,
				"minecraft_version": minecraft_version,
				"loader": loader,
				"loader_version": loader_version,
			}
		)
	sorted_records = sorted(records, key=lambda item: item["version_id"].casefold())
	_installed_versions_cache = list(sorted_records)
	return sorted_records


def verify_installed_version(version_id: str) -> bool:
	if not version_id or Path(version_id).name != version_id:
		return False
	version_root = Path(minecraft_directory) / "versions"
	metadata_path = version_root / version_id / f"{version_id}.json"
	jar_path = version_root / version_id / f"{version_id}.jar"
	try:
		metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
		if not isinstance(metadata, dict) or metadata.get("id") != version_id:
			return False
		parent_version = metadata.get("inheritsFrom")
		if parent_version and not verify_installed_version(parent_version):
			return False
		downloads = metadata.get("downloads")
		client_download = downloads.get("client") if isinstance(downloads, dict) else None
		if jar_path.is_file():
			client_sha1 = (
				client_download.get("sha1")
				if isinstance(client_download, dict)
				else None
			)
			if isinstance(client_sha1, str) and client_sha1:
				digest = hashlib.sha1()
				with jar_path.open("rb") as jar_file:
					for chunk in iter(lambda: jar_file.read(1024 * 1024), b""):
						digest.update(chunk)
				return digest.hexdigest() == client_sha1.lower()
			return True
		return bool(parent_version)
	except (OSError, UnicodeError, TypeError, ValueError):
		return False


def _loader_version_from_id(
	version_id: str, prefix: str, minecraft_version: str
) -> str:
	remainder = version_id[len(prefix) :]
	suffix = f"-{minecraft_version}"
	return remainder[: -len(suffix)] if remainder.endswith(suffix) else remainder


def get_external_installations() -> list[dict[str, str]]:
	profiles_path = Path(minecraft_directory) / "launcher_profiles.json"
	try:
		document = json.loads(profiles_path.read_text(encoding="utf-8"))
	except (OSError, UnicodeError, json.JSONDecodeError):
		return []

	profiles = document.get("profiles", {}) if isinstance(document, dict) else {}
	if not isinstance(profiles, dict):
		return []

	external = []
	for profile_id, profile in profiles.items():
		if not isinstance(profile, dict) or profile.get("type") != "custom":
			continue
		external.append(
			{
				"id": str(profile_id),
				"name": str(
					profile.get("name")
					or profile.get("lastVersionId")
					or "External installation"
				),
				"version_id": str(
					profile.get("lastVersionId") or "Version not selected"
				),
			}
		)
	return external


def import_external_installations(
	installations: list[dict[str, str]],
	handled_profile_ids: set[str],
	installed_versions: list[dict[str, str]],
	external_profiles: list[dict[str, str]],
	version_catalog: list[dict],
) -> tuple[list[dict[str, str]], set[str], bool]:
	installed_by_id = {
		version["version_id"]: version for version in installed_versions
	}
	known_profile_versions = {
		profile.get("version_id") or profile.get("minecraft_version")
		for profile in installations
	}
	version_types = {version["id"]: version.get("type") for version in version_catalog}
	imported_installations = list(installations)
	handled_ids = set(handled_profile_ids)
	changed = False

	for external_profile in external_profiles:
		external_id = external_profile["id"]
		if external_id in handled_ids:
			continue

		version_id = external_profile["version_id"]
		version_info = installed_by_id.get(version_id)
		if version_info is None:
			continue

		handled_ids.add(external_id)
		changed = True
		if version_id in known_profile_versions:
			continue

		imported_installations.append(
			{
				"id": f"minecraft-launcher:{external_id}",
				"name": external_profile["name"],
				"loader": version_info["loader"],
				"channel": (
					"Snapshot"
					if version_types.get(version_info["minecraft_version"]) == "snapshot"
					else "Release"
				),
				"minecraft_version": version_info["minecraft_version"],
				"loader_version": version_info["loader_version"],
				"version_id": version_id,
				"source": "minecraft_launcher",
				"external_profile_id": external_id,
			}
		)
		known_profile_versions.add(version_id)

	if changed:
		invalidate_installed_versions_cache()
	return imported_installations, handled_ids, changed


def get_mod_loader(loader_name: str):
	return minecraft_launcher_lib.mod_loader.get_mod_loader(loader_name.casefold())


async def start_game(
	target,
	username: str,
	online_available: bool,
	callback,
	settings=None,
	cancel_event=None,
):
	version_id = target["version_id"]
	installed_ids = {item["version_id"] for item in get_installed_versions()}
	if version_id not in installed_ids:
		if not online_available:
			raise RuntimeError(
				"This version is not installed. Reconnect to install it."
			)
		def install_callback_handler(name):
			def handler(*args):
				if cancel_event is not None and cancel_event.is_set():
					raise DownloadCancelled("Version download cancelled.")
				callback.get(name, lambda *_: None)(*args)
				if cancel_event is not None and cancel_event.is_set():
					raise DownloadCancelled("Version download cancelled.")
			return handler

		install_callback = {
			name: install_callback_handler(name)
			for name in ("setStatus", "setProgress", "setMax")
		}
		pending_versions = load_pending_installations()
		pending_versions.add(version_id)
		if not save_pending_installations(pending_versions):
			raise RuntimeError("Could not record the pending version installation.")
		if target["loader"] == "Vanilla":
			await asyncio.to_thread(
				minecraft_launcher_lib.install.install_minecraft_version,
				target["minecraft_version"],
				minecraft_directory,
				install_callback,
			)
		else:
			version_id = await asyncio.to_thread(
				get_mod_loader(target["loader"]).install,
				target["minecraft_version"],
				minecraft_directory,
				loader_version=target["loader_version"],
				callback=install_callback,
			)
		pending_versions.add(version_id)
		if not save_pending_installations(pending_versions):
			raise RuntimeError("Could not record the completed version for verification.")
		if cancel_event is not None and cancel_event.is_set():
			raise DownloadCancelled("Version download cancelled.")
		if not verify_installed_version(version_id):
			raise RuntimeError(
				"Minecraft installation did not pass verification; it will be repaired on the next launch."
			)
		pending_versions.discard(version_id)
		pending_versions.discard(target["version_id"])
		if not save_pending_installations(pending_versions):
			raise RuntimeError("Could not save the verified installation state.")
		invalidate_installed_versions_cache()

	callback["setStatus"]("Building launch command...")
	game_dir = str(target.get("game_directory") or minecraft_directory)
	Path(game_dir).mkdir(parents=True, exist_ok=True)
	launch_options = {
		"username": username,
		"uuid": generate_offline_uuid(username),
		"token": "",
		"launcherName": "Gr8 Launcher",
		"launcherVersion": "1.0",
		"gameDirectory": game_dir,
		"customResolution": True,
		"resolutionWidth": str(
			int((settings or {}).get("window_width", 854))
		),
		"resolutionHeight": str(
			int((settings or {}).get("window_height", 480))
		),
		"jvmArguments": [
			f"-Xms256M",
			f"-Xmx{int((settings or {}).get('ram_mb', 4096))}M",
		],
	}
	java_path = str((settings or {}).get("java_path", "")).strip()
	if java_path:
		launch_options["executablePath"] = java_path
	command = await asyncio.to_thread(
		minecraft_launcher_lib.command.get_minecraft_command,
		version_id,
		minecraft_directory,
		launch_options,
	)
	if (settings or {}).get("fullscreen", False):
		command.append("--fullscreen")
	callback["setStatus"]("Starting Minecraft...")
	log_file = None
	process_options = {}
	if (settings or {}).get("capture_logs", True):
		log_path = Path(minecraft_directory) / "logs" / "gr8-launcher.log"
		log_path.parent.mkdir(parents=True, exist_ok=True)
		log_file = log_path.open("a", encoding="utf-8", errors="replace")
	try:
		process = await asyncio.to_thread(
			subprocess.Popen,
			command,
			cwd=game_dir,
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
			encoding="utf-8",
			errors="replace",
			bufsize=1,
			**process_options,
		)
	except Exception:
		if log_file is not None:
			log_file.close()
		raise
	return process, log_file
