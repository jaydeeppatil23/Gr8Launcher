"""Evaluate native mod version requirements without a new dependency."""

from __future__ import annotations

import re


def supported_mod_loaders(minecraft_version: str, loader: str) -> tuple[str, ...]:
    """Provider loader tags supported by this instance, in preference order."""
    loader = loader.lower()
    if loader == "quilt":
        return ("quilt", "fabric")
    if loader == "neoforge" and minecraft_version == "1.20.1":
        return ("neoforge", "forge")
    return (loader,) if loader else ()


def version_supports_profile(version: dict, minecraft_version: str, loader: str) -> bool:
    """Use the same Minecraft/loader policy for browsing, planning, and updates."""
    return (not version.get("_server_only") and minecraft_version in version.get("game_versions", [])
            and bool(set(supported_mod_loaders(minecraft_version, loader))
                     .intersection(version.get("loaders", []))))


def _version(value: str) -> tuple[tuple[int, ...], str | None]:
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]*))?(?:\+[0-9A-Za-z.-]+)?", value)
    if not match:
        raise ValueError("Unsupported semantic version.")
    return tuple(int(part) for part in match[1].split(".")), match[2]


def _compare(left: tuple, right: tuple) -> int:
    count = max(len(left[0]), len(right[0]))
    a = left[0] + (0,) * (count - len(left[0]))
    b = right[0] + (0,) * (count - len(right[0]))
    if a != b:
        return (a > b) - (a < b)
    a_pre, b_pre = left[1], right[1]
    if a_pre == b_pre:
        return 0
    if a_pre is None or b_pre is None:
        return 1 if a_pre is None else -1
    if not a_pre or not b_pre:
        return 1 if a_pre else -1
    for a_part, b_part in zip(a_pre.split("."), b_pre.split(".")):
        if a_part == b_part:
            continue
        if a_part.isdigit() and b_part.isdigit():
            compared = (int(a_part) > int(b_part)) - (int(a_part) < int(b_part))
            if compared:
                return compared
            continue
        if a_part.isdigit() != b_part.isdigit():
            return -1 if a_part.isdigit() else 1
        return (a_part > b_part) - (a_part < b_part)
    return (len(a_pre.split(".")) > len(b_pre.split("."))) - (len(a_pre.split(".")) < len(b_pre.split(".")))


def fabric_loader_satisfies(version: str, requirement: object) -> bool | None:
    """Strings are intersections; Fabric's array of strings is a union.

    Return None for unsupported metadata rather than guessing compatibility.
    Fabric's ^ operator keeps the major component, including major zero.
    """
    predicates = requirement if isinstance(requirement, list) else [requirement]
    if not predicates or any(not isinstance(item, str) for item in predicates):
        return None
    if any(not predicate.strip() or predicate.strip() == "*" for predicate in predicates):
        return True
    try:
        current = _version(version)
        alternatives = []
        for predicate in predicates:
            matches = []
            for term in predicate.split():
                if term == "*":
                    continue
                match = re.fullmatch(r"(>=|<=|>|<|=|~|\^)?(.+)", term)
                operator, reference = match[1] or "=", match[2]
                wildcard = re.fullmatch(r"(\d+(?:\.\d+)*)\.(?:x|X|\*)(?:\.(?:x|X|\*))*", reference)
                if wildcard:
                    if operator != "=":
                        return None
                    components = tuple(int(part) for part in wildcard[1].split("."))
                    floor = (components, "")
                    ceiling = (components[:-1] + (components[-1] + 1,), "")
                    matches.append(_compare(current, floor) >= 0 and _compare(current, ceiling) < 0)
                    continue
                bound = _version(reference)
                compared = _compare(current, bound)
                if operator in ("~", "^"):
                    position = 0 if operator == "^" else 1
                    components = bound[0] + (0,) * max(0, position + 1 - len(bound[0]))
                    actual = current[0] + (0,) * max(0, position + 1 - len(current[0]))
                    matches.append(compared >= 0 and actual[:position + 1] == components[:position + 1])
                else:
                    matches.append({"=": compared == 0, ">": compared > 0, "<": compared < 0,
                                    ">=": compared >= 0, "<=": compared <= 0}[operator])
            alternatives.append(all(matches))
        return any(alternatives)
    except (ValueError, TypeError, AttributeError):
        return None


def mod_version_satisfies(version: str, requirement: object, *, maven: bool = False) -> bool | None:
    """Check Fabric/Quilt predicates or numeric Forge Maven ranges conservatively."""
    if isinstance(requirement, dict):
        if set(requirement) == {"any"} and isinstance(requirement["any"], list):
            values = [mod_version_satisfies(version, item) for item in requirement["any"]]
            return True if True in values else None if None in values else False
        if set(requirement) == {"all"} and isinstance(requirement["all"], list):
            values = [mod_version_satisfies(version, item) for item in requirement["all"]]
            return False if False in values else None if None in values else True
        return None
    if not maven:
        # Fabric also allows exact, non-semantic version strings.
        if isinstance(requirement, str) and requirement == version:
            return True
        return fabric_loader_satisfies(version, requirement)
    if not isinstance(requirement, str):
        return None
    if requirement.strip() in ("", "*", "(,)"):
        return True
    ranges = re.findall(r"[\[(][^\[\]()]*[\])]", requirement)
    if not ranges or ",".join(ranges).replace(" ", "") != requirement.replace(" ", ""):
        # Bare Maven versions are recommendations, not hard constraints.
        return True if re.fullmatch(r"[0-9A-Za-z.+_-]+", requirement) else None
    try:
        current = _version(version)
        for interval in ranges:
            bounds = interval[1:-1].split(",")
            if len(bounds) == 1:
                if interval[0] != "[" or interval[-1] != "]":
                    return None
                if _compare(current, _version(bounds[0].strip())) == 0:
                    return True
            elif len(bounds) == 2:
                lower, upper = (bound.strip() for bound in bounds)
                low = _compare(current, _version(lower)) if lower else 1
                high = _compare(current, _version(upper)) if upper else -1
                if (low > 0 or low == 0 and interval[0] == "[") and (high < 0 or high == 0 and interval[-1] == "]"):
                    return True
            else:
                return None
        return False
    except ValueError:
        return None


def quilt_dependency_matches(dependency: object, providers: dict[str, list[dict]], *,
                             conflict: bool = False, depth: int = 0) -> bool | None:
    """Quilt arrays mean OR in depends/unless, AND in breaks; support conditions."""
    if depth >= 20:
        return None
    if isinstance(dependency, list):
        values = [quilt_dependency_matches(item, providers, conflict=conflict, depth=depth + 1)
                  for item in dependency]
        if conflict:
            return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    if isinstance(dependency, str):
        dependency = {"id": dependency}
    if not isinstance(dependency, dict) or not isinstance(dependency.get("id"), str):
        return None
    unless = dependency.get("unless")
    if unless is not None:
        ignored = quilt_dependency_matches(unless, providers, depth=depth + 1)
        if ignored is True:
            return not conflict
        if ignored is None:
            return None
    candidates = providers.get(dependency["id"], [])
    if not candidates and dependency.get("optional"):
        return not conflict
    values = [mod_version_satisfies(item["version"], dependency.get("versions", "*")) for item in candidates]
    return True if True in values else None if None in values else False


def quilt_dependency_targets(dependency: object, *, depth: int = 0) -> set[str]:
    """IDs whose changes can affect a Quilt dependency or its unless condition."""
    if depth >= 20:
        return set()
    if isinstance(dependency, str):
        return {dependency}
    if isinstance(dependency, list):
        return {target for item in dependency for target in quilt_dependency_targets(item, depth=depth + 1)}
    if isinstance(dependency, dict):
        targets = {dependency["id"]} if isinstance(dependency.get("id"), str) else set()
        return targets | quilt_dependency_targets(dependency.get("unless"), depth=depth + 1)
    return set()
