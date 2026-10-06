import asyncio
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import requests
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
		if not isinstance(profile, dict) or profile.get("type") != "custom":
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
					profile.get("name")
					or profile.get("lastVersionId")
					or "External installation"
				),
				"version_id": str(
					profile.get("lastVersionId") or "Version not selected"
				),
				"is_instance": is_instance,
				"instance_name": instance_name,
				"game_directory": resolved_game_dir,
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
	installed_by_id = {
		version["version_id"]: version for version in installed_versions
	}
	known_profile_versions = {
		profile.get("version_id") or profile.get("minecraft_version")
		for profile in installations
		if not profile.get("is_instance")
	}
	known_game_dirs = {
		str(Path(p["game_directory"]).resolve())
		for p in installations
		if p.get("game_directory")
	}
	version_types = {version["id"]: version.get("type") for version in version_catalog}
	imported_installations = list(installations)
	handled_ids = set(handled_profile_ids)
	changed = False

	# Upgrade any existing installations if their external profile has instance/custom dir info
	ext_by_id = {p["id"]: p for p in external_profiles}
	for inst in imported_installations:
		ext_id = inst.get("external_profile_id")
		if ext_id and ext_id in ext_by_id:
			ext_prof = ext_by_id[ext_id]
			if ext_prof.get("is_instance") and not inst.get("is_instance"):
				inst["is_instance"] = True
				inst["instance_name"] = str(ext_prof.get("instance_name", ""))
				inst["game_directory"] = str(ext_prof.get("game_directory", ""))
				changed = True
				if inst["game_directory"]:
					try:
						known_game_dirs.add(str(Path(inst["game_directory"]).resolve()))
					except Exception:
						pass

	for external_profile in external_profiles:
		external_id = str(external_profile["id"])
		if external_id in handled_ids:
			continue

		version_id = str(external_profile["version_id"])
		version_info = installed_by_id.get(version_id)
		if version_info is None:
			continue

		handled_ids.add(external_id)
		changed = True
		is_inst = bool(external_profile.get("is_instance", False))
		ext_game_dir = str(external_profile.get("game_directory", "") or "")
		if is_inst and ext_game_dir:
			try:
				if str(Path(ext_game_dir).resolve()) in known_game_dirs:
					continue
			except Exception:
				pass
		elif not is_inst and version_id in known_profile_versions:
			continue

		imported_installations.append(
			{
				"id": f"minecraft-launcher:{external_id}",
				"name": str(external_profile["name"]),
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
			}
		)
		if ext_game_dir:
			try:
				known_game_dirs.add(str(Path(ext_game_dir).resolve()))
			except Exception:
				pass
		if not is_inst:
			known_profile_versions.add(version_id)

	if changed:
		invalidate_installed_versions_cache()
	return imported_installations, handled_ids, changed


def _detect_instance_configuration(
	folder: Path,
	installed_by_id: dict[str, dict[str, str]],
	installed_ids: set[str],
	version_catalog: list[dict],
) -> dict[str, str] | None:
	mc_version = None
	loader = "Vanilla"
	loader_version = ""

	# 1. Custom instance metadata if present
	for meta_name in ("instance.json", "profile.json", "minecraft_instance.json"):
		meta_file = folder / meta_name
		if meta_file.is_file():
			try:
				meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
				if isinstance(meta_data, dict):
					mc_version = meta_data.get("minecraft_version") or meta_data.get("mc_version") or mc_version
					loader = meta_data.get("loader") or loader
					loader_version = meta_data.get("loader_version") or loader_version
			except Exception:
				pass

	# 2. Check logs (latest.log or recent logs)
	log_files = []
	latest_log = folder / "logs" / "latest.log"
	if latest_log.is_file():
		log_files.append(latest_log)
	logs_dir = folder / "logs"
	if logs_dir.is_dir():
		log_files.extend(sorted(logs_dir.glob("*.log"), reverse=True)[:3])

	for log_file in log_files:
		if mc_version and loader != "Vanilla" and loader_version:
			break
		try:
			with log_file.open("r", encoding="utf-8", errors="ignore") as f:
				for _ in range(80):
					line = f.readline()
					if not line:
						break
					m = re.search(
						r"Loading Minecraft\s+([^\s]+)\s+with\s+(\w+)\s+Loader\s+([^\s]+)",
						line,
						re.IGNORECASE,
					)
					if m:
						mc_version = m.group(1)
						loader = m.group(2).capitalize()
						loader_version = m.group(3)
						break
					m2 = re.search(r"Loading Minecraft\s+([^\s]+)", line, re.IGNORECASE)
					if m2 and not mc_version:
						mc_version = m2.group(1)
					if "Fabric Loader" in line:
						loader = "Fabric"
					elif "NeoForge" in line:
						loader = "NeoForge"
					elif "Forge" in line:
						loader = "Forge"
		except Exception:
			pass

	# 3. Indicator directories
	if loader == "Vanilla":
		if (folder / ".fabric").exists():
			loader = "Fabric"
		elif (folder / ".quilt").exists():
			loader = "Quilt"

	# 4. Check mods folder
	mods_dir = folder / "mods"
	if mods_dir.is_dir():
		for mod in mods_dir.glob("*.jar"):
			m_mod = re.search(
				r"[+\-_]mc([0-9]+\.[0-9]+(?:\.[0-9]+)?)", mod.name, re.IGNORECASE
			)
			if m_mod and not mc_version:
				mc_version = m_mod.group(1)
			mod_lower = mod.name.lower()
			if "fabric" in mod_lower and loader == "Vanilla":
				loader = "Fabric"
			elif "neoforge" in mod_lower:
				loader = "NeoForge"
			elif "forge" in mod_lower and loader == "Vanilla":
				loader = "Forge"

	# 5. Check folder name for clues if version is still missing
	if not mc_version:
		clean_name = folder.name.replace("_", ".")
		for v_id in sorted(installed_ids, key=len, reverse=True):
			if v_id in clean_name or clean_name in v_id:
				if v_id in installed_by_id:
					v_info = installed_by_id[v_id]
					mc_version = v_info["minecraft_version"]
					if loader == "Vanilla":
						loader = v_info["loader"]
						loader_version = v_info.get("loader_version", "")
				break

	# 6. Resolve version_id against installed_versions
	version_id = None
	if loader == "Fabric":
		if loader_version and mc_version:
			target_id = f"fabric-loader-{loader_version}-{mc_version}"
			if target_id in installed_ids:
				version_id = target_id
		if not version_id and mc_version:
			for v_id in installed_ids:
				if v_id.startswith("fabric-loader-") and v_id.endswith(f"-{mc_version}"):
					version_id = v_id
					v_info = installed_by_id[v_id]
					loader_version = v_info.get("loader_version", loader_version)
					break
		if not version_id and mc_version:
			version_id = f"fabric-loader-{loader_version or '0.16.0'}-{mc_version}"
	elif loader == "NeoForge" and mc_version:
		for v_id in installed_ids:
			if "neoforge" in v_id.lower() and mc_version in v_id:
				version_id = v_id
				break
	elif loader == "Forge" and mc_version:
		for v_id in installed_ids:
			if "forge" in v_id.lower() and mc_version in v_id:
				version_id = v_id
				break
	elif loader == "Vanilla" and mc_version:
		version_id = mc_version

	if not version_id and mc_version:
		version_id = mc_version

	if not version_id:
		for v_id in installed_ids:
			if v_id.lower() in folder.name.lower():
				version_id = v_id
				v_info = installed_by_id[v_id]
				mc_version = v_info["minecraft_version"]
				loader = v_info["loader"]
				loader_version = v_info.get("loader_version", "")
				break

	if not version_id:
		is_mc_folder = any(
			(folder / item).exists()
			for item in ("saves", "mods", "resourcepacks", "options.txt", "config")
		)
		if not is_mc_folder:
			return None
		if installed_ids:
			first_id = sorted(installed_ids)[0]
			version_id = first_id
			v_info = installed_by_id.get(first_id, {})
			mc_version = v_info.get("minecraft_version", first_id)
			loader = v_info.get("loader", "Vanilla")
			loader_version = v_info.get("loader_version", "")
		else:
			return None

	if version_id in installed_by_id:
		v_info = installed_by_id[version_id]
		mc_version = v_info["minecraft_version"]
		loader = v_info["loader"]
		loader_version = v_info.get("loader_version", loader_version)

	return {
		"version_id": version_id,
		"minecraft_version": mc_version or version_id,
		"loader": loader,
		"loader_version": loader_version,
	}


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


def estimate_installation_total(target: dict, mc_dir: str | Path) -> int:
	mc_dir = str(mc_dir)
	version_id = target.get("minecraft_version") or target.get("version_id", "")
	v_json_path = os.path.join(mc_dir, "versions", version_id, f"{version_id}.json")
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
			r = requests.get(manifest_url, timeout=3).json()
			entry = next((v for v in r.get("versions", []) if v.get("id") == version_id), None)
			if entry and "url" in entry:
				vdata = requests.get(entry["url"], timeout=3).json()
		except Exception:
			pass

	if not vdata:
		return 0

	libs_count = len(vdata.get("libraries", []))
	assets_count = 0
	asset_info = vdata.get("assetIndex")
	if asset_info:
		asset_id = vdata.get("assets", asset_info.get("id", ""))
		asset_local = os.path.join(mc_dir, "assets", "indexes", f"{asset_id}.json")
		if os.path.isfile(asset_local):
			try:
				with open(asset_local, "r", encoding="utf-8") as f:
					assets_count = len(json.load(f).get("objects", {}))
			except Exception:
				assets_count = 1500
		else:
			try:
				aindex = requests.get(asset_info["url"], timeout=3).json()
				assets_count = len(aindex.get("objects", {}))
			except Exception:
				assets_count = 3500

	runtime_count = 0
	if "javaVersion" in vdata:
		comp = vdata["javaVersion"].get("component", "java-runtime-delta")
		try:
			plat = minecraft_launcher_lib.runtime._get_jvm_platform_string()
			v_file = os.path.join(mc_dir, "runtime", comp, plat, ".version")
			if not os.path.isfile(v_file):
				runtime_count = 250
		except Exception:
			pass

	return libs_count + assets_count + runtime_count


class InstallationProgressTracker:
	def __init__(self, estimated_total: int = 0):
		self.estimated_total = max(0, estimated_total)
		self.completed_previous = 0
		self.current_stage_max = 0
		self.current_stage_val = 0
		self.highest_ratio = 0.0
		self.start_time = None
		self.history = []
		self.stage_name = "Setup"
		self.stage_index = 0
		self.last_status = "Preparing..."
		self.last_eta = ""

	def on_status(self, raw_status: str):
		self.last_status = raw_status
		lower = raw_status.lower()
		if "librar" in lower:
			self.stage_name = "Libraries"
			self.stage_index = 1
		elif "asset" in lower:
			self.stage_name = "Assets"
			self.stage_index = 2
		elif "runtime" in lower or "java" in lower:
			self.stage_name = "Java Runtime"
			self.stage_index = 3

	def on_max(self, maximum: int):
		if self.current_stage_max > 0:
			self.completed_previous += max(self.current_stage_val, self.current_stage_max)
		self.current_stage_max = max(1, int(maximum) + 1)
		self.current_stage_val = 0

	def on_progress(self, current: int):
		now = time.monotonic()
		if self.start_time is None:
			self.start_time = now

		self.current_stage_val = max(0, int(current))
		overall_completed = self.completed_previous + self.current_stage_val
		effective_total = max(self.estimated_total, self.completed_previous + self.current_stage_max)
		if effective_total <= 0:
			effective_total = max(1, overall_completed)

		if self.estimated_total > 0:
			ratio = overall_completed / effective_total
		else:
			stage_ranges = {1: (0.0, 0.20), 2: (0.20, 0.80), 3: (0.80, 0.98)}
			if self.stage_index in stage_ranges:
				s_start, s_end = stage_ranges[self.stage_index]
				stage_pct = self.current_stage_val / max(1, self.current_stage_max)
				ratio = s_start + (s_end - s_start) * stage_pct
			else:
				ratio = self.highest_ratio

		ratio = min(0.99, max(self.highest_ratio, ratio))
		self.highest_ratio = ratio

		progress_advanced = not self.history or overall_completed > self.history[-1][1]
		if progress_advanced:
			self.history.append((now, overall_completed))
		self.history = [h for h in self.history if now - h[0] <= 4.0]

		eta_text = ""
		if progress_advanced and len(self.history) >= 2:
			dt = now - self.history[0][0]
			df = overall_completed - self.history[0][1]
			if dt >= 0.6 and df > 0:
				speed = df / dt
				remaining = max(0, effective_total - overall_completed)
				if remaining > 0 and speed > 0:
					eta_sec = int(round(remaining / speed))
					if eta_sec < 60:
						time_str = f"{eta_sec}s"
					elif eta_sec < 3600:
						m, s = divmod(eta_sec, 60)
						time_str = f"{m}m {s:02d}s"
					else:
						h, m = divmod(eta_sec, 3600)
						time_str = f"{h}h {m // 60:02d}m"
					speed_str = (
						f"{speed:.0f}"
						if speed >= 10
						else f"{speed:.1f}"
						if speed >= 1
						else f"{speed:.2f}"
					)
					eta_text = f"ETA: {time_str} ({speed_str} files/s)"
				elif remaining == 0:
					eta_text = "Finishing..."
		if not eta_text and not self.last_eta and (now - self.start_time) > 1.5:
			eta_text = "ETA: calculating..."

		if eta_text:
			self.last_eta = eta_text
		pct_text = f"{int(round(ratio * 100))}%"
		if self.current_stage_max > 1:
			stage_str = f"Downloading {self.stage_name} ({self.current_stage_val:,}/{self.current_stage_max:,})"
		else:
			stage_str = self.last_status

		status_text = f"{stage_str} • {pct_text}"
		return ratio, status_text, self.last_eta


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
	except Exception:
		if log_file is not None:
			log_file.close()
		raise
	return process, log_file
