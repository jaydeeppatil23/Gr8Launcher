import asyncio
import ctypes
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import requests
import minecraft_launcher_lib
import ely_auth
from storage import (
	load_pending_installations,
	save_pending_installations,
	get_active_account,
	update_account_session,
	get_data_dir,
	load_latest_release_id,
)
from skins import (
	prepare_skin_launch,
	render_head_avatar_bytes,
	sync_playerdata_uuids,
)
import banners


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
	vanilla_releases = [
		version
		for version in releases
		if not any(
			mod in version.get("id", "").lower()
			for mod in ("fabric", "forge", "quilt", "neoforge", "optifine")
		)
	]
	candidate_releases = vanilla_releases if vanilla_releases else releases
	latest = max(
		candidate_releases, key=lambda version: version["releaseTime"], default=None
	)
	return versions, latest["id"] if latest else None


def get_version_catalog() -> tuple[list[dict], str | None, bool]:
	try:
		versions = minecraft_launcher_lib.utils.get_version_list()
		latest_release = minecraft_launcher_lib.utils.get_latest_version()["release"]
		return versions, latest_release, True
	except Exception:
		versions, latest_release = get_local_version_catalog()
		return versions, latest_release, False


def get_latest_release_version() -> tuple[str | None, bool]:
	"""Resolve Mojang's latest release, retaining a usable offline fallback."""
	try:
		with requests.get(
			"https://launchermeta.mojang.com/mc/game/version_manifest_v2.json",
			timeout=5,
		) as response:
			response.raise_for_status()
			latest = response.json()["latest"]["release"]
			if isinstance(latest, str) and latest.strip():
				return latest.strip(), True
	except (requests.RequestException, ValueError, KeyError, TypeError):
		pass

	cached = load_latest_release_id()
	if cached:
		return cached, False

	versions, _ = get_local_version_catalog()
	vanilla_ids = {
		version["version_id"] for version in get_installed_versions()
		if version["loader"] == "Vanilla"
	}
	latest_local = max(
		(version for version in versions
		 if version.get("type") == "release" and version["id"] in vanilla_ids),
		key=lambda version: version["releaseTime"], default=None,
	)
	return (latest_local["id"] if latest_local else None), False


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
		elif "optifine" in main_class or "optifine" in lowered_id:
			loader = "OptiFine"
			loader_version = version_id.split("-OptiFine_", 1)[-1] if "-OptiFine_" in version_id else (version_id.split("-optifine_", 1)[-1] if "-optifine_" in version_id else "OptiFine")

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


def save_base64_profile_icon(profile_id: str, base64_str: str) -> str | None:
	"""Decodes a base64 or data:image/... URI icon and saves it as a profile icon file."""
	if not base64_str or not isinstance(base64_str, str):
		return None
	raw_b64 = base64_str.strip()
	ext = "png"
	if raw_b64.startswith("data:image/") and ";base64," in raw_b64:
		header, raw_b64 = raw_b64.split(";base64,", 1)
		sub_ext = header.replace("data:image/", "").split("+")[0].strip().lower()
		if sub_ext in ("png", "jpg", "jpeg", "webp", "ico"):
			ext = sub_ext
	try:
		padded = raw_b64 + "=" * (-len(raw_b64) % 4)
		import base64 as b64_module
		image_bytes = b64_module.b64decode(padded)
		if not image_bytes or len(image_bytes) < 8:
			return None
		safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(profile_id))
		icons_dir = get_data_dir() / "icons"
		icons_dir.mkdir(parents=True, exist_ok=True)
		target_file = icons_dir / f"icon_{safe_id}.{ext}"
		target_file.write_bytes(image_bytes)
		return str(target_file)
	except Exception:
		return None


def delete_external_profile(external_id: str) -> bool:
	"""Removes a profile from launcher_profiles.json matching external_id."""
	if not external_id:
		return False
	profiles_path = Path(minecraft_directory) / "launcher_profiles.json"
	if not profiles_path.is_file():
		return False
	try:
		document = json.loads(profiles_path.read_text(encoding="utf-8"))
		if not isinstance(document, dict):
			return False
		profiles = document.get("profiles")
		if not isinstance(profiles, dict):
			return False
		matched_key = None
		if external_id in profiles:
			matched_key = external_id
		else:
			for k, p in profiles.items():
				if isinstance(p, dict) and p.get("id") == external_id:
					matched_key = k
					break
		if not matched_key:
			return False
		del profiles[matched_key]
		if document.get("selectedProfile") == matched_key:
			document["selectedProfile"] = next(iter(profiles.keys()), "")
		temp_file = profiles_path.with_suffix(".tmp")
		temp_file.write_text(json.dumps(document, indent=2), encoding="utf-8")
		temp_file.replace(profiles_path)
		return True
	except Exception:
		return False


def get_external_installations() -> list[dict[str, object]]:
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
		if not isinstance(profile, dict):
			continue

		ptype = str(profile.get("type", "")).strip().lower()
		last_ver = str(profile.get("lastVersionId") or "").strip()
		pname = str(profile.get("name") or "").strip()

		if not last_ver:
			continue
		if ptype in ("latest-release", "latest-snapshot") and not pname:
			continue

		game_dir = profile.get("gameDir")
		is_instance = False
		instance_name = ""
		resolved_game_dir = ""
		if isinstance(game_dir, str) and game_dir.strip():
			try:
				p_game_dir = Path(game_dir).resolve()
				p_mc_dir = Path(minecraft_directory).resolve()
				if p_game_dir != p_mc_dir:
					is_instance = True
					resolved_game_dir = str(p_game_dir)
					instance_name = p_game_dir.name
			except Exception:
				pass

		external.append(
			{
				"id": str(profile_id),
				"name": str(
					pname
					or last_ver
					or "External installation"
				),
				"version_id": last_ver,
				"is_instance": is_instance,
				"instance_name": instance_name,
				"game_directory": resolved_game_dir,
				"icon": str(profile.get("icon") or ""),
				"java_args": str(profile.get("javaArgs") or ""),
			}
		)
	return external


def import_external_installations(
	installations: list[dict[str, object]],
	handled_profile_ids: set[str],
	installed_versions: list[dict[str, str]],
	external_profiles: list[dict[str, object]],
	version_catalog: list[dict],
) -> tuple[list[dict[str, object]], set[str], bool]:
	"""Import new profiles without changing settings of existing installations."""
	installed_by_id = {
		version["version_id"]: version for version in installed_versions
	}
	version_types = {version["id"]: version.get("type") for version in version_catalog}
	imported_installations = list(installations)
	handled_ids = set(handled_profile_ids)
	changed = False

	for external_profile in external_profiles:
		external_id = str(external_profile["id"])
		version_id = str(external_profile["version_id"])
		version_info = installed_by_id.get(version_id)
		if version_info is None:
			ver_path = Path(minecraft_directory) / "versions" / version_id / f"{version_id}.json"
			if ver_path.is_file():
				try:
					vmeta = json.loads(ver_path.read_text(encoding="utf-8"))
					mc_ver = vmeta.get("inheritsFrom", version_id)
					main_cls = vmeta.get("mainClass", "").lower()
					lowered = version_id.lower()
					ldr = "Vanilla"
					ldr_ver = ""
					if "fabric" in main_cls or lowered.startswith("fabric-loader-"):
						ldr = "Fabric"
						ldr_ver = _loader_version_from_id(version_id, "fabric-loader-", mc_ver)
					elif "quilt" in main_cls or lowered.startswith("quilt-loader-"):
						ldr = "Quilt"
						ldr_ver = _loader_version_from_id(version_id, "quilt-loader-", mc_ver)
					elif "neoforge" in main_cls or lowered.startswith("neoforge-"):
						ldr = "NeoForge"
						ldr_ver = _loader_version_from_id(version_id, "neoforge-", mc_ver)
					elif "forge" in main_cls or "-forge-" in lowered:
						ldr = "Forge"
						ldr_ver = version_id.rsplit("-forge-", 1)[-1]
					elif "optifine" in main_cls or "optifine" in lowered:
						ldr = "OptiFine"
						ldr_ver = version_id.split("-OptiFine_", 1)[-1] if "-OptiFine_" in version_id else (version_id.split("-optifine_", 1)[-1] if "-optifine_" in version_id else "OptiFine")
					version_info = {
						"version_id": version_id,
						"minecraft_version": mc_ver,
						"loader": ldr,
						"loader_version": ldr_ver,
					}
					installed_by_id[version_id] = version_info
				except Exception:
					pass
		if version_info is None:
			continue

		is_inst = bool(external_profile.get("is_instance", False))
		ext_game_dir = str(external_profile.get("game_directory", "") or "")
		ext_name = str(external_profile.get("name") or version_info["minecraft_version"])

		matched_inst = None
		for inst in imported_installations:
			if inst.get("external_profile_id") == external_id or inst.get("id") == f"minecraft-launcher:{external_id}":
				matched_inst = inst
				break
			if is_inst and inst.get("is_instance") and ext_game_dir:
				try:
					if get_profile_game_directory(inst) == Path(ext_game_dir).resolve():
						matched_inst = inst
						break
				except Exception:
					pass
			elif not is_inst and not inst.get("is_instance") and inst.get("version_id") == version_id:
				matched_inst = inst
				break

		# A shared game folder is not permission to replace a locally pinned loader.
		# Imported profiles can also have been customized since their first import.
		if matched_inst is not None:
			if external_id not in handled_ids:
				handled_ids.add(external_id)
				changed = True
			continue

		icon_data = str(external_profile.get("icon") or "").strip()
		custom_banner_path = None
		custom_icon_path = None
		if icon_data.startswith("data:image/") or len(icon_data) > 100:
			# Decode base64 icon and save it as custom icon and banner
			custom_banner_path = banners.save_base64_icon_as_banner(f"minecraft-launcher:{external_id}", icon_data)
			if not custom_banner_path:
				custom_banner_path = banners.save_base64_icon_as_banner(external_id, icon_data)
			custom_icon_path = save_base64_profile_icon(f"minecraft-launcher:{external_id}", icon_data)
			if not custom_icon_path:
				custom_icon_path = save_base64_profile_icon(external_id, icon_data)

		if external_id not in handled_ids:
			imported_installations.append(
				{
					"id": f"minecraft-launcher:{external_id}",
					"name": ext_name,
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
					"is_instance": is_inst,
					"instance_name": str(external_profile.get("instance_name", "")),
					"game_directory": ext_game_dir,
					"icon": custom_icon_path or "",
					"custom_banner": custom_banner_path or "",
				}
			)
			handled_ids.add(external_id)
			changed = True

	if changed:
		invalidate_installed_versions_cache()
	return imported_installations, handled_ids, changed


def _detect_instance_configuration(
	folder: Path,
	installed_by_id: dict[str, dict[str, str]],
	installed_ids: set[str],
	version_catalog: list[dict],
) -> dict[str, str] | None:
	"""Discover only configurations supported by metadata or unambiguous evidence."""
	metadata = {}
	for name in ("instance.json", "profile.json", "minecraft_instance.json"):
		try:
			document = json.loads((folder / name).read_text(encoding="utf-8"))
			if isinstance(document, dict):
				for key in ("version_id", "minecraft_version", "loader", "loader_version"):
					value = document.get(key)
					if isinstance(value, str) and value.strip():
						metadata.setdefault(key, value.strip())
				if isinstance(document.get("mc_version"), str):
					metadata.setdefault("minecraft_version", document["mc_version"])
		except (OSError, UnicodeError, ValueError):
			pass

	if metadata.get("version_id"):
		installed = installed_by_id.get(metadata["version_id"])
		if installed:
			if any(metadata.get(key) and metadata[key].casefold() != str(installed.get(key, "")).casefold()
				   for key in ("minecraft_version", "loader", "loader_version")):
				return None
			return dict(installed)

	configuration = dict(metadata)
	logs_dir = folder / "logs"
	log_files = [logs_dir / "latest.log"]
	if logs_dir.is_dir():
		log_files.extend(sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:3])
	for log_file in log_files:
		try:
			with log_file.open(encoding="utf-8", errors="replace") as stream:
				for _ in range(80):
					line = stream.readline()
					if not line:
						break
					match = re.search(r"Loading Minecraft\s+(\S+)\s+with\s+(\w+)\s+Loader\s+(\S+)", line, re.IGNORECASE)
					if match:
						evidence = dict(zip(("minecraft_version", "loader", "loader_version"), match.groups()))
						# Logs from another configuration must not supply part of a metadata pin.
						if all(not metadata.get(key) or metadata[key].casefold() == value.casefold()
							   for key, value in evidence.items()):
							for key, value in evidence.items():
								configuration.setdefault(key, value)
						break
		except OSError:
			pass

	if not configuration.get("loader"):
		for indicator, loader_name in ((".fabric", "Fabric"), (".quilt", "Quilt")):
			if (folder / indicator).exists():
				configuration["loader"] = loader_name
				break
		if not configuration.get("loader"):
			match = re.match(r"(fabric|quilt|neoforge|forge|vanilla)(?:[ _.-]|$)", folder.name, re.IGNORECASE)
			if match:
				configuration["loader"] = match.group(1)
			elif (folder / "mods").is_dir():
				return None

	# An exact installed ID in the folder name is useful; partial names are not pins.
	if not configuration.get("minecraft_version"):
		candidates = [info for version_id, info in installed_by_id.items()
					  if version_id.casefold() == folder.name.replace("_", ".").casefold()]
		if len(candidates) == 1:
			for key in ("minecraft_version", "loader", "loader_version"):
				configuration.setdefault(key, candidates[0].get(key, ""))
		else:
			match = re.search(r"(?<![0-9.])([0-9]+\.[0-9]+(?:\.[0-9]+)?)(?![0-9.])", folder.name.replace("_", "."))
			if match:
				configuration["minecraft_version"] = match.group(1)

	mc_version = configuration.get("minecraft_version")
	loader = configuration.get("loader", "Vanilla")
	loader = {"fabric": "Fabric", "quilt": "Quilt", "forge": "Forge", "neoforge": "NeoForge", "vanilla": "Vanilla"}.get(loader.casefold(), loader)
	loader_version = configuration.get("loader_version", "")
	if not mc_version:
		return None
	if loader != "Vanilla" and not loader_version:
		return None
	result = {"minecraft_version": mc_version, "loader": loader, "loader_version": loader_version}
	try:
		version_id = get_profile_version_id(result)
	except (KeyError, ValueError):
		return None
	if not version_id or (metadata.get("version_id") and metadata["version_id"] != version_id):
		return None
	result["version_id"] = version_id
	return result


def discover_local_instances(
	mc_dir: str | Path,
	installations: list[dict[str, object]],
	installed_versions: list[dict[str, str]],
	version_catalog: list[dict],
) -> tuple[list[dict[str, object]], bool]:
	instances_root = Path(mc_dir) / "instances"
	if not instances_root.is_dir():
		return installations, False

	existing_game_dirs = set()
	for item in installations:
		gd = item.get("game_directory")
		if gd:
			try:
				existing_game_dirs.add(str(Path(str(gd)).resolve()))
			except Exception:
				existing_game_dirs.add(str(gd))
		iname = item.get("instance_name")
		if iname:
			try:
				existing_game_dirs.add(str((instances_root / str(iname)).resolve()))
			except Exception:
				pass

	existing_ids = {str(item["id"]) for item in installations}
	installed_by_id = {v["version_id"]: v for v in installed_versions}
	installed_ids = set(installed_by_id.keys())
	version_types = {v["id"]: v.get("type") for v in version_catalog}

	updated_installations = list(installations)
	changed = False

	for folder in sorted(instances_root.iterdir()):
		if not folder.is_dir():
			continue
		try:
			folder_resolved = str(folder.resolve())
		except Exception:
			folder_resolved = str(folder)

		if folder_resolved in existing_game_dirs:
			continue

		detected = _detect_instance_configuration(
			folder, installed_by_id, installed_ids, version_catalog
		)
		if not detected:
			continue

		profile_id = f"instance:{folder.name}"
		if profile_id in existing_ids:
			profile_id = f"instance:{uuid.uuid4().hex[:8]}"

		new_profile = {
			"id": profile_id,
			"name": folder.name.replace("_", " "),
			"loader": detected["loader"],
			"channel": (
				"Snapshot"
				if version_types.get(detected["minecraft_version"]) == "snapshot"
				else "Release"
			),
			"minecraft_version": detected["minecraft_version"],
			"loader_version": detected["loader_version"],
			"version_id": detected["version_id"],
			"is_instance": True,
			"instance_name": folder.name,
			"game_directory": folder_resolved,
			"source": "discovered_instance",
		}

		updated_installations.append(new_profile)
		existing_game_dirs.add(folder_resolved)
		existing_ids.add(profile_id)
		changed = True

	return updated_installations, changed


def resolve_installation_files(target: dict, mc_dir: str | Path) -> int:
	mc_dir = str(mc_dir)
	version_id = target.get("minecraft_version") or target.get("version_id", "")
	v_dir = os.path.join(mc_dir, "versions", version_id)
	v_json_path = os.path.join(v_dir, f"{version_id}.json")
	vdata = None

	if os.path.isfile(v_json_path):
		try:
			with open(v_json_path, "r", encoding="utf-8") as f:
				vdata = json.load(f)
		except Exception:
			pass

	if not vdata:
		try:
			manifest_url = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
			r = requests.get(manifest_url, timeout=4).json()
			entry = next((v for v in r.get("versions", []) if v.get("id") == version_id), None)
			if entry and "url" in entry:
				vdata = requests.get(entry["url"], timeout=4).json()
				os.makedirs(v_dir, exist_ok=True)
				with open(v_json_path, "w", encoding="utf-8") as f:
					json.dump(vdata, f)
		except Exception:
			pass

	if not vdata:
		return 0

	libs_count = len(vdata.get("libraries", []))
	assets_count = 0
	asset_info = vdata.get("assetIndex")
	if asset_info:
		asset_id = vdata.get("assets", asset_info.get("id", ""))
		asset_dir = os.path.join(mc_dir, "assets", "indexes")
		asset_local = os.path.join(asset_dir, f"{asset_id}.json")
		if os.path.isfile(asset_local):
			try:
				with open(asset_local, "r", encoding="utf-8") as f:
					assets_count = len(json.load(f).get("objects", {}))
			except Exception:
				pass
		else:
			try:
				aindex = requests.get(asset_info["url"], timeout=4).json()
				assets_count = len(aindex.get("objects", {}))
				os.makedirs(asset_dir, exist_ok=True)
				with open(asset_local, "w", encoding="utf-8") as f:
					json.dump(aindex, f)
			except Exception:
				pass

	runtime_count = 0
	if "javaVersion" in vdata:
		comp = vdata["javaVersion"].get("component", "java-runtime-delta")
		try:
			plat = minecraft_launcher_lib.runtime._get_jvm_platform_string()
			v_file = os.path.join(mc_dir, "runtime", comp, plat, ".version")
			if not os.path.isfile(v_file):
				runtime_count = 150
		except Exception:
			pass

	loader_count = 0
	if target.get("loader") and target["loader"] != "Vanilla":
		loader_count = 30

	return libs_count + assets_count + runtime_count + loader_count


# Backward-compatible alias
estimate_installation_total = resolve_installation_files


class InstallationProgressTracker:
	def __init__(self, total_files: int = 0, is_modded: bool = False):
		self._lock = threading.Lock()
		self.is_modded = bool(is_modded)
		self.pass_num = 0
		self.stage_name = "Setup"
		self.highest_ratio = 0.0
		self.current_ratio = 0.0
		self.current_stage_val = 0
		self.current_stage_max = 0
		self.start_time = None
		self.history = []
		self.ema_speed = None
		self.last_status = "Preparing..."
		self.last_eta = ""
		self.is_finished = False

	def on_status(self, raw_status: str):
		with self._lock:
			self.last_status = str(raw_status)
			lower = self.last_status.lower()
			if "complete" in lower:
				self.pass_num += 1
				return

			if "installer" in lower:
				self.stage_name = "Mod Installer"
			elif "librar" in lower:
				self.stage_name = "Libraries"
			elif "asset" in lower:
				self.stage_name = "Assets"
			elif "runtime" in lower or "java" in lower:
				self.stage_name = "Java Runtime"
			elif any(k in lower for k in ("forge", "fabric", "quilt", "neoforge", "mod")):
				self.stage_name = "Mod Loader"
			elif "verif" in lower:
				self.stage_name = "Verifying"

	def on_max(self, maximum: int):
		with self._lock:
			self.current_stage_max = max(1, int(maximum) + 1)
			self.current_stage_val = 0

	def on_progress(self, current: int):
		now = time.monotonic()
		with self._lock:
			if self.start_time is None:
				self.start_time = now

			self.current_stage_val = max(self.current_stage_val, int(current))
			self.current_stage_max = max(self.current_stage_max, self.current_stage_val)
			self.history.append((now, self.current_stage_val))
			self.history = [h for h in self.history if now - h[0] <= 2.5]

			if not self.is_modded:
				ranges = {
					"setup": (0.0, 0.05),
					"libraries": (0.05, 0.20),
					"assets": (0.20, 0.90),
					"java runtime": (0.90, 0.98),
					"verifying": (0.98, 0.99),
				}
			else:
				if self.pass_num == 0:
					ranges = {
						"setup": (0.0, 0.02),
						"libraries": (0.02, 0.12),
						"assets": (0.12, 0.65),
						"java runtime": (0.65, 0.70),
					}
				elif self.pass_num == 1:
					ranges = {
						"mod installer": (0.70, 0.75),
						"libraries": (0.75, 0.82),
						"mod loader": (0.75, 0.82),
						"assets": (0.80, 0.85),
						"java runtime": (0.82, 0.85),
					}
				else:
					ranges = {
						"libraries": (0.85, 0.95),
						"assets": (0.92, 0.96),
						"mod loader": (0.90, 0.97),
						"verifying": (0.97, 0.99),
					}

			s_key = self.stage_name.lower()
			r_low, r_high = ranges.get(s_key, (self.highest_ratio, min(0.98, self.highest_ratio + 0.08)))
			stage_pct = self.current_stage_val / max(self.current_stage_max, 1)
			calculated_ratio = r_low + (r_high - r_low) * stage_pct

			# Strictly monotonic: ratio NEVER drops or goes backward
			self.highest_ratio = min(0.99, max(self.highest_ratio, calculated_ratio))

			if len(self.history) >= 2:
				dt = now - self.history[0][0]
				df = self.current_stage_val - self.history[0][1]
				if dt >= 0.4 and df > 0:
					instant_speed = df / dt
					if self.ema_speed is None:
						self.ema_speed = instant_speed
					else:
						self.ema_speed = 0.25 * instant_speed + 0.75 * self.ema_speed

		return self.get_display_state()

	def get_display_state(self) -> tuple[float, str, str]:
		now = time.monotonic()
		with self._lock:
			if self.is_finished:
				return 1.0, "Installation complete • 100%", ""

			diff = self.highest_ratio - self.current_ratio
			if abs(diff) < 0.001:
				self.current_ratio = self.highest_ratio
			else:
				self.current_ratio += diff * 0.35

			render_ratio = min(0.99, max(0.0, self.current_ratio))

			eta_text = ""
			if self.ema_speed and self.ema_speed > 0.1:
				speed_str = (
					f"{self.ema_speed:.0f}"
					if self.ema_speed >= 10
					else f"{self.ema_speed:.1f}"
				)
				eta_text = f"({speed_str} files/s)"

			pct_text = f"{int(round(render_ratio * 100))}%"
			val_disp = min(self.current_stage_val, self.current_stage_max)
			max_disp = max(self.current_stage_max, val_disp)
			if max_disp > 1:
				status_text = (
					f"Downloading {self.stage_name} ({val_disp:,} / {max_disp:,}) • {pct_text}"
				)
			elif self.last_status.startswith("Download "):
				fname = self.last_status[9:].strip()
				status_text = f"Downloading {fname} • {pct_text}"
			else:
				status_text = f"{self.last_status} • {pct_text}"

			return render_ratio, status_text, eta_text

	def finish(self):
		with self._lock:
			self.is_finished = True
			self.current_ratio = 1.0
			self.highest_ratio = 1.0
			self.last_eta = ""


def get_mod_loader(loader_name: str):
	return minecraft_launcher_lib.mod_loader.get_mod_loader(loader_name.casefold())


def get_profile_version_id(profile):
	"""Resolve older profiles that store Minecraft and loader versions separately."""
	version_id = profile.get("version_id")
	if version_id:
		return version_id
	if str(profile.get("loader", "Vanilla")).casefold() == "vanilla":
		return profile.get("minecraft_version", "")
	if not profile.get("loader_version"):
		return ""
	try:
		return get_mod_loader(profile["loader"]).get_installed_version(
			profile["minecraft_version"], profile.get("loader_version", "")
		)
	except (KeyError, ValueError):
		return ""


def get_profile_game_directory(profile, mc_dir=None):
	"""Resolve an instance folder without falling back to a different game folder."""
	root = Path(mc_dir or minecraft_directory).resolve()
	game_dir = str(profile.get("game_directory") or "").strip()
	if not game_dir and profile.get("instance_name"):
		name = str(profile["instance_name"])
		if name in (".", "..") or Path(name).name != name or "/" in name or "\\" in name:
			raise ValueError("Invalid instance folder name.")
		game_dir = str(root / "instances" / name)
	if profile.get("is_instance") and not game_dir:
		raise ValueError("This instance has no game folder. Select its existing folder before launching.")
	resolved = Path(game_dir).resolve() if game_dir else root
	if profile.get("is_instance") and resolved in (root, root / "instances"):
		raise ValueError("This instance must use its own game folder.")
	return resolved


def prepare_launch_profile(profile, installed_versions):
	"""Require consistent, pinned versions before an install can select a loader."""
	target = dict(profile)
	version_id = get_profile_version_id(target)
	if not version_id:
		raise ValueError("Choose a Minecraft version and a specific loader version for this profile.")
	target["version_id"] = version_id
	installed = next((v for v in installed_versions if v["version_id"] == version_id), None)
	if installed:
		for key in ("loader", "minecraft_version", "loader_version"):
			actual = installed.get(key, "")
			configured = target.get(key)
			if configured and str(configured).casefold() != str(actual).casefold():
				raise ValueError("This profile's version ID conflicts with its Minecraft or loader version. Correct the profile before launching.")
			target[key] = actual
	elif str(target.get("loader", "Vanilla")).casefold() != "vanilla":
		if not target.get("loader_version"):
			raise ValueError("Select a specific loader version before installing or repairing this profile.")
		expected = get_mod_loader(target["loader"]).get_installed_version(
			target["minecraft_version"], target["loader_version"]
		)
		if version_id != expected:
			raise ValueError("This profile's version ID conflicts with its pinned loader version.")
	elif version_id != target.get("minecraft_version"):
		raise ValueError("This profile's version ID conflicts with its Minecraft version.")
	return target


async def start_game(
	target,
	username: str,
	online_available: bool,
	callback,
	settings=None,
	cancel_event=None,
):
	game_folder = get_profile_game_directory(target)
	if target.get("is_instance") and not game_folder.is_dir():
		raise FileNotFoundError(f"Instance folder is missing: {game_folder}. Restore it or select its existing folder before launching.")
	installed_versions = get_installed_versions()
	target = prepare_launch_profile(target, installed_versions)
	version_id = target["version_id"]
	installed_ids = {item["version_id"] for item in installed_versions}
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
			installed_version_id = await asyncio.to_thread(
				get_mod_loader(target["loader"]).install,
				target["minecraft_version"],
				minecraft_directory,
				loader_version=target["loader_version"],
				callback=install_callback,
			)
			if installed_version_id != version_id:
				raise RuntimeError("The installer returned a different version than the pinned profile. Launch stopped.")
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
	game_dir = str(game_folder)
	if not target.get("is_instance"):
		game_folder.mkdir(parents=True, exist_ok=True)

	active_account = await asyncio.to_thread(get_active_account, include_credentials=online_available)
	account_type = active_account.get("type", "offline")
	account_username = str(active_account.get("username", username)).strip() or username or "Player"

	offline_uuid = generate_offline_uuid(account_username)
	active_uuid = offline_uuid
	active_token = ""
	sensitive_values = []
	skin_server_instance = None

	if account_type == "ely":
		ely_uuid = str(active_account.get("uuid", "")).replace("-", "").strip()
		access_token = str(active_account.get("access_token", "")).strip()
		client_token = str(active_account.get("client_token", "")).strip()
		sensitive_values.extend((access_token, client_token))

		if ely_uuid:
			active_uuid = ely_uuid

		if online_available and access_token:
			callback["setStatus"]("Verifying Ely.by session...")
			is_valid = await asyncio.to_thread(ely_auth.validate_token, access_token, client_token)
			if not is_valid and client_token:
				callback["setStatus"]("Refreshing Ely.by session...")
				refreshed = await asyncio.to_thread(ely_auth.refresh_token, access_token, client_token)
				if refreshed and refreshed.get("access_token"):
					sensitive_values.extend((refreshed["access_token"], refreshed.get("client_token") or client_token))
					updated = await asyncio.to_thread(
						update_account_session, active_account["id"], access_token, refreshed,
					)
					if not updated:
						# Removal or re-login may have happened during the request.
						# Never restore that account or retain the discarded token.
						await asyncio.to_thread(
							ely_auth.invalidate_token, refreshed["access_token"],
							refreshed.get("client_token") or client_token,
						)
						refreshed = None
					if refreshed and refreshed.get("access_token"):
						active_account["access_token"] = refreshed["access_token"]
						if refreshed.get("uuid"):
							active_account["uuid"] = refreshed["uuid"]
							active_uuid = str(refreshed["uuid"]).replace("-", "")
						if refreshed.get("username"):
							active_account["username"] = refreshed["username"]
							account_username = refreshed["username"]
						access_token = refreshed["access_token"]
						client_token = refreshed.get("client_token") or client_token
					else:
						access_token = ""
				else:
					access_token = ""
			active_token = access_token
		if online_available and not active_token:
			callback["setStatus"]("Ely.by session unavailable; playing offline. Sign in again for multiplayer.")

	launch_options = {
		"username": account_username,
		"uuid": active_uuid,
		"token": active_token,
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

	# Skin system is ONLY active for Ely.by accounts.
	# Offline mode strictly disables custom skins and skin bridge (defaults to vanilla Steve/Alex).
	if account_type == "ely" and (settings or {}).get("enable_custom_skins", True):
		active_uuid, skin_jvm_args, skin_server_instance = prepare_skin_launch(
			username=account_username,
			game_dir=game_dir,
			minecraft_dir=minecraft_directory,
			is_online=online_available,
		)
		launch_options["jvmArguments"].extend(skin_jvm_args)
		launch_options["uuid"] = active_uuid

	profile_jvm = str((target or {}).get("jvm_arguments") or "").strip()
	if profile_jvm:
		try:
			launch_options["jvmArguments"].extend(shlex.split(profile_jvm))
		except Exception:
			launch_options["jvmArguments"].extend(profile_jvm.split())
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

	if os.name == "nt" and command:
		exe_path = Path(command[0])
		if exe_path.name.lower() == "java.exe":
			javaw_path = exe_path.with_name("javaw.exe")
			if javaw_path.exists():
				command[0] = str(javaw_path)
	elif os.name != "nt" and command:
		try:
			java_bin = Path(command[0])
			if java_bin.exists() and not os.access(java_bin, os.X_OK):
				java_bin.chmod(java_bin.stat().st_mode | 0o755)
		except Exception:
			pass

	callback["setStatus"]("Starting Minecraft...")
	log_file = None
	process_options = {}
	if os.name == "nt":
		# Only use CREATE_NO_WINDOW if the executable is console java.exe (javaw.exe does not need it)
		# Do NOT use STARTUPINFO with SW_HIDE, because Windows forces SW_HIDE on Minecraft's first game window!
		if command and Path(command[0]).name.lower() == "java.exe":
			process_options["creationflags"] = subprocess.CREATE_NO_WINDOW

	if (settings or {}).get("capture_logs", True):
		log_path = Path(minecraft_directory) / "logs" / "gr8-launcher.log"
		log_path.parent.mkdir(parents=True, exist_ok=True)
		log_file = log_path.open("a", encoding="utf-8", errors="replace")
		log_file.write(f"\n[Gr8 Launcher] Profile: {target.get('id', '')}; version: {version_id}; game directory: {game_dir}\n")
		log_file.flush()
	try:
		process = await asyncio.to_thread(
			subprocess.Popen,
			command,
			cwd=game_dir,
			stdin=subprocess.DEVNULL,
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
			encoding="utf-8",
			errors="replace",
			bufsize=1,
			**process_options,
		)
		process.gr8_sensitive_values = tuple(value for value in sensitive_values if value)
		process.gr8_presence_player = {
			"username": account_username,
			"type": account_type if (settings or {}).get("enable_custom_skins", True) else "offline",
		}
		process.gr8_presence_face = None
		cached_skin_bytes = getattr(skin_server_instance, "cached_bytes", None)
		if cached_skin_bytes and (settings or {}).get("discord_rpc", True):
			try:
				process.gr8_presence_face = render_head_avatar_bytes(cached_skin_bytes, scale=1)
			except (OSError, ValueError):
				pass
	except Exception:
		if log_file is not None:
			log_file.close()
		raise

	def _sync_on_exit():
		try:
			process.wait()
			if skin_server_instance is not None:
				skin_server_instance.stop()
			if active_uuid != offline_uuid:
				sync_playerdata_uuids(game_dir, active_uuid, offline_uuid)
				if game_dir != str(minecraft_directory):
					sync_playerdata_uuids(minecraft_directory, active_uuid, offline_uuid)
		except Exception:
			pass

	threading.Thread(target=_sync_on_exit, daemon=True).start()

	return process, log_file
