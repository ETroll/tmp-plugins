#!/usr/bin/env python3
"""
Copy pinned skills from a skill registry into Agent Plugins 1.0 packages.

For every plugin.json matched by SKILL_SYNC_GLOB, this script reads

    extensions["<namespace>"]["skills"] = {
        "<name>": {
            "repo":   "Owner/repo",
            "source": "skills/expres/<name>",   # directory in the registry
            "dest":   "skills/<name>",          # optional; relative to the plugin root
            "ref":    "<40-hex sha>"
        }
    }

and, in sync mode, copies each referenced directory from the registry to its
destination inside the plugin root. "dest" defaults to skills/<name>, which is where
harnesses discover skills; any other destination inside the plugin root is allowed
for content that is not itself a skill (shared references, assets). "path" is
accepted as an alias for "source". A lock file is written to <plugin>/<namespace>/skills.lock.json
so that check mode can confirm the final state of a pull request: every referenced
skill present, at the pinned commit, and byte-identical to the registry.

Only standard library modules are used, so the action needs nothing installed.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MODE = os.environ.get("SKILL_SYNC_MODE", "sync").strip().lower()
PLUGINS_GLOB = os.environ.get("SKILL_SYNC_GLOB", "plugins/*/plugin.json")
NAMESPACE = os.environ.get("SKILL_SYNC_NAMESPACE", "com.akerbp.skillreposync")
BUMP_VERSION = os.environ.get("SKILL_SYNC_BUMP", "true").strip().lower() == "true"
SERVER = os.environ.get("SKILL_SYNC_SERVER", "https://github.com").rstrip("/")
TOKEN = os.environ.get("REGISTRY_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""

LOCK_FILE = "skills.lock.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHORT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
EXPAND_REFS = os.environ.get("SKILL_SYNC_EXPAND_REFS", "true").strip().lower() == "true"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Agent Skills names: lowercase alphanumerics and hyphens is the conservative subset.
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class SyncError(Exception):
    pass


# ---------------------------------------------------------------- logging

def log(msg: str) -> None:
    print(msg, flush=True)


def error(msg: str) -> None:
    # GitHub Actions annotation, shows up in the PR checks UI.
    print(f"::error::{msg}", flush=True)


def notice(msg: str) -> None:
    print(f"::notice::{msg}", flush=True)


def set_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as fh:
        if "\n" in value:
            fh.write(f"{name}<<__EOF__\n{value}\n__EOF__\n")
        else:
            fh.write(f"{name}={value}\n")


# ---------------------------------------------------------------- helpers

def read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def content_hash(directory: Path) -> str:
    """Deterministic hash of every regular file under directory (path + bytes)."""
    h = hashlib.sha256()
    for file in sorted(p for p in directory.rglob("*") if p.is_file()):
        rel = file.relative_to(directory).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(file.read_bytes())
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def remove_tree(plugin_dir: Path, rel: str) -> None:
    """Remove a vendored directory and any parent directories left empty, up to the plugin root."""
    target = plugin_dir / rel
    shutil.rmtree(target, ignore_errors=True)
    parent = target.parent
    while parent != plugin_dir and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def assert_no_symlinks(directory: Path) -> None:
    for p in directory.rglob("*"):
        if p.is_symlink():
            raise SyncError(f"symlink found in vendored skill: {p}")


def find_skill_md(directory: Path) -> Path | None:
    """Find SKILL.md directly inside directory, tolerating case.

    Registry contributors on Windows commit through a case-insensitive,
    case-preserving filesystem: once a file lands as skill.md, renaming it to
    SKILL.md case-only is awkward there (it needs a two-step rename through a
    temporary name for git to see it as a change). So the *source* lookup here
    is case-insensitive. The vendored copy is still normalized to exactly
    SKILL.md by the caller, because harness discovery requires that exact name
    (Agent Plugins spec: a path "named exactly SKILL.md").
    """
    # Always scan for every case-variant first -- an early exact-match shortcut would let
    # SKILL.md and a stray skill.md coexist undetected, defeating the ambiguity check below.
    matches = [p for p in directory.iterdir() if p.is_file() and p.name.lower() == "skill.md"]
    if len(matches) > 1:
        raise SyncError(
            f"{directory}: multiple files match SKILL.md case-insensitively: "
            f"{', '.join(sorted(m.name for m in matches))}"
        )
    return matches[0] if matches else None


def parse_skill_name(skill_md: Path) -> str | None:
    """Read the `name:` field from SKILL.md YAML frontmatter, if present."""
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    for line in text[3:end].splitlines():
        m = re.match(r"^\s*name\s*:\s*(.+?)\s*$", line)
        if m:
            return m.group(1).strip().strip("'\"")
    return None


def bump_patch(version: str) -> str:
    m = SEMVER_RE.match(version)
    if not m:
        raise SyncError(
            f"cannot bump non-semver version {version!r}; set a MAJOR.MINOR.PATCH version"
        )
    major, minor, patch = (int(x) for x in m.groups())
    return f"{major}.{minor}.{patch + 1}"


# ---------------------------------------------------------------- ref resolution

_resolved: dict[tuple[str, str], str] = {}


def api_base() -> str:
    if SERVER == "https://github.com":
        return "https://api.github.com"
    return SERVER + "/api/v3"


def resolve_ref(repo: str, ref: str) -> str:
    """Expand an abbreviated commit SHA to the full 40-character SHA.

    Users copy short SHAs from the GitHub UI, and git will not fetch a commit by an
    abbreviated id, so we ask the GitHub API to resolve it first. Full SHAs are
    returned as-is. Results are cached per (repo, ref) for the run.
    """
    ref = ref.strip().lower()
    if SHA_RE.match(ref):
        return ref
    key = (repo, ref)
    if key in _resolved:
        return _resolved[key]

    if SERVER.startswith("file://"):
        # Local testing against a file:// registry: no API, so resolve with a full fetch.
        full = _resolve_via_git(repo, ref)
    else:
        full = _resolve_via_api(repo, ref)

    if not SHA_RE.match(full or ""):
        raise SyncError(f"{repo}: could not resolve ref {ref!r} to a commit")
    _resolved[key] = full
    return full


def _resolve_via_api(repo: str, ref: str) -> str:
    import urllib.error
    import urllib.request

    url = f"{api_base()}/repos/{repo}/commits/{ref}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "skill-sync-action",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SyncError(f"{repo}: ref {ref!r} not found (not a commit in that repository, or ambiguous)")
        if exc.code in (401, 403):
            raise SyncError(f"{repo}: not allowed to resolve ref {ref!r}; the token needs read access to the registry")
        raise SyncError(f"{repo}: GitHub API error {exc.code} while resolving {ref!r}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SyncError(f"{repo}: could not reach GitHub API to resolve {ref!r}: {exc}")
    return str(data.get("sha", ""))


def _resolve_via_git(repo: str, ref: str) -> str:
    url = f"{SERVER}/{repo}.git"
    with tempfile.TemporaryDirectory(prefix="skill-registry-resolve-") as tmp:
        run(["git", "clone", "-q", "--bare", "--filter=blob:none", url, tmp])
        out = run(["git", "-C", tmp, "rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
        return out.stdout.strip()


# ---------------------------------------------------------------- registry fetch

def fetch_skill(repo: str, ref: str, path: str, dest: Path) -> None:
    """Fetch exactly one commit from the registry and extract one path into dest."""
    url = f"{SERVER}/{repo}.git"
    if TOKEN:
        scheme, rest = url.split("://", 1)
        url = f"{scheme}://x-access-token:{TOKEN}@{rest}"

    with tempfile.TemporaryDirectory(prefix="skill-registry-") as tmp:
        git = ["git", "-C", tmp]
        run(["git", "init", "-q", tmp])
        run(git + ["remote", "add", "origin", url])
        # GitHub allows fetching a reachable commit by its full SHA.
        run(git + ["-c", "protocol.version=2", "fetch", "-q", "--depth", "1", "origin", ref])

        # Confirm the path exists at that commit before extracting.
        ls = run(git + ["ls-tree", "-d", ref, "--", path], check=False)
        if ls.returncode != 0 or not ls.stdout.strip():
            raise SyncError(f"{repo}@{ref[:7]}: path {path!r} is not a directory at that commit")

        dest.mkdir(parents=True, exist_ok=True)
        depth = len(Path(path).parts)
        with subprocess.Popen(
            git + ["archive", "--format=tar", ref, "--", path],
            stdout=subprocess.PIPE,
        ) as archive:
            assert archive.stdout is not None  # guaranteed by stdout=PIPE
            tar = subprocess.run(
                ["tar", "-x", "-C", str(dest), f"--strip-components={depth}"],
                stdin=archive.stdout,
                capture_output=True,
                text=True,
            )
            archive.stdout.close()
            archive.wait()
        if archive.returncode != 0 or tar.returncode != 0:
            raise SyncError(f"{repo}@{ref[:7]}: failed to extract {path!r}: {tar.stderr.strip()}")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        # Never echo the token.
        safe = [re.sub(r"x-access-token:[^@]+@", "x-access-token:***@", c) for c in cmd]
        raise SyncError(f"command failed: {' '.join(safe)}\n{result.stderr.strip()}")
    return result


# ---------------------------------------------------------------- validation

def normalize(spec: dict, name: str) -> dict:
    """Return a copy of spec with source/dest resolved (path alias, default dest)."""
    out = dict(spec)
    if "source" not in out and "path" in out:
        out["source"] = out.pop("path")
    out.setdefault("dest", f"skills/{name}")
    return out


def check_relative(plugin_dir: Path, name: str, label: str, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise SyncError(f"{plugin_dir}: skill {name!r} is missing {label!r}")
    p = Path(value)
    if p.is_absolute() or ".." in p.parts or p.as_posix() != value.strip("/") or value.strip("/") == "":
        raise SyncError(f"{plugin_dir}: skill {name!r} has invalid {label} {value!r} (must be a relative directory path)")
    return p


def validate_references(plugin_dir: Path, refs: dict) -> dict:
    """Validate and normalize every reference. Returns name -> normalized spec."""
    if not isinstance(refs, dict):
        raise SyncError(f"{plugin_dir}: extensions.{NAMESPACE}.skills must be an object")
    normalized: dict = {}
    dests: dict[str, str] = {}
    for name, raw in refs.items():
        if not NAME_RE.match(name):
            raise SyncError(f"{plugin_dir}: invalid skill name {name!r}")
        if not isinstance(raw, dict):
            raise SyncError(f"{plugin_dir}: skill {name!r} must be an object")
        spec = normalize(raw, name)
        for key in ("repo", "ref"):
            if not isinstance(spec.get(key), str) or not spec[key]:
                raise SyncError(f"{plugin_dir}: skill {name!r} is missing {key!r}")
        if not REPO_RE.match(spec["repo"]):
            raise SyncError(f"{plugin_dir}: skill {name!r} has invalid repo {spec['repo']!r} (expected owner/repo)")
        spec["ref"] = spec["ref"].strip().lower()
        if not SHORT_SHA_RE.match(spec["ref"]):
            raise SyncError(
                f"{plugin_dir}: skill {name!r} ref must be a commit SHA (at least 7 hex characters), "
                f"got {spec['ref']!r}; branch names and tags are not allowed"
            )
        check_relative(plugin_dir, name, "source", spec.get("source"))
        dest = check_relative(plugin_dir, name, "dest", spec["dest"])
        dest_str = dest.as_posix()
        # The destination must be a directory inside the plugin, and must not be one of the
        # reserved package files or the skills/ directory itself.
        if dest_str in ("skills", "plugin.json", "mcp.json", NAMESPACE) or dest.parts[0] == NAMESPACE:
            raise SyncError(f"{plugin_dir}: skill {name!r} has a reserved dest {dest_str!r}")
        # Destinations must not nest inside each other; otherwise one sync deletes another's files.
        for other, other_dest in dests.items():
            if dest_str == other_dest or dest_str.startswith(other_dest + "/") or other_dest.startswith(dest_str + "/"):
                raise SyncError(f"{plugin_dir}: dest of {name!r} ({dest_str}) overlaps dest of {other!r} ({other_dest})")
        dests[name] = dest_str
        spec["dest"] = dest_str
        normalized[name] = spec
    return normalized


def is_skill_dest(dest: str) -> bool:
    """True when dest is skills/<x>, i.e. a directory a harness will load as a skill."""
    parts = Path(dest).parts
    return len(parts) == 2 and parts[0] == "skills"


# ---------------------------------------------------------------- per-plugin work

def process_plugin(manifest_path: Path) -> bool:
    """Returns True if the plugin was modified (sync mode) or is out of date (check mode)."""
    plugin_dir = manifest_path.parent
    manifest = read_json(manifest_path)
    refs = (manifest.get("extensions") or {}).get(NAMESPACE, {}).get("skills")
    lock_path = plugin_dir / NAMESPACE / LOCK_FILE
    lock = read_json(lock_path) if lock_path.exists() else {"skills": {}}
    locked: dict = lock.get("skills", {})

    if not refs:
        if not locked:
            log(f"{plugin_dir}: no registry skills declared")
            return False
        if MODE == "check":
            error(f"{plugin_dir}: lock file lists vendored skills but the manifest declares none")
            return True
        for stale, prev in locked.items():
            remove_tree(plugin_dir, prev.get("dest", f"skills/{stale}"))
            log(f"{plugin_dir}: removed no-longer-referenced skill {stale}")
        lock_path.unlink()
        parent = lock_path.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
        maybe_bump(plugin_dir, manifest_path, manifest)
        return True

    refs = validate_references(plugin_dir, refs)
    drift: list[str] = []
    changed = False
    manifest_dirty = False
    new_lock = {"skills": {}}

    # Skills that were vendored before but are no longer referenced.
    for stale in set(locked) - set(refs):
        if MODE == "sync":
            remove_tree(plugin_dir, locked[stale].get("dest", f"skills/{stale}"))
            log(f"{plugin_dir}: removed no-longer-referenced skill {stale}")
            changed = True
        else:
            drift.append(f"skill {stale!r} is locked but no longer referenced")

    for name, spec in refs.items():
        dest = spec["dest"]
        target = plugin_dir / dest
        prev = locked.get(name)

        # A moved destination: remove the old copy so it does not linger.
        if MODE == "sync" and prev and prev.get("dest", f"skills/{name}") != dest:
            remove_tree(plugin_dir, prev.get("dest", f"skills/{name}"))
            log(f"{plugin_dir}: {name} dest moved from {prev.get('dest')} to {dest}")
            changed = True
            prev = None

        if MODE == "check":
            if prev is None:
                drift.append(
                    f"skill {name!r} is referenced but has not been synced from the registry yet"
                    + (" (a local copy is present and will be replaced)" if target.is_dir() else "")
                )
                continue
            ref_matches = str(prev.get("ref", "")).startswith(spec["ref"])
            if not ref_matches or any(prev.get(k) != spec[k] for k in ("repo", "source", "dest")):
                drift.append(f"skill {name!r}: manifest reference changed since last sync")
                continue
            if not target.is_dir():
                drift.append(f"skill {name!r}: {dest} is missing")
                continue
            if content_hash(target) != prev.get("hash"):
                drift.append(
                    f"skill {name!r}: {dest} differs from the registry version at "
                    f"{spec['ref'][:7]}; the sync action will replace it (edit the skill in the registry instead)"
                )
            continue

        # ---- sync mode
        full_ref = resolve_ref(spec["repo"], spec["ref"])
        if full_ref != spec["ref"]:
            log(f"{plugin_dir}: {name} ref {spec['ref']} resolves to {full_ref}")
            if EXPAND_REFS:
                # Write the full SHA back so the manifest is unambiguous from here on.
                manifest["extensions"][NAMESPACE]["skills"][name]["ref"] = full_ref
                manifest_dirty = True
        spec["ref"] = full_ref

        with tempfile.TemporaryDirectory(prefix="skill-") as tmp:
            staged = Path(tmp) / name
            fetch_skill(spec["repo"], spec["ref"], spec["source"], staged)
            assert_no_symlinks(staged)

            # Only a directory placed directly under skills/ is loaded as a skill by the
            # harness, so only there do we require a SKILL.md whose name matches the folder.
            if is_skill_dest(dest):
                skill_md = find_skill_md(staged)
                if skill_md is None:
                    raise SyncError(f"{plugin_dir}: {spec['repo']}:{spec['source']} has no SKILL.md")
                if skill_md.name != "SKILL.md":
                    notice(
                        f"{plugin_dir}: {spec['repo']}:{spec['source']} has {skill_md.name!r}; "
                        "normalized to SKILL.md in the vendored copy"
                    )
                    skill_md = skill_md.rename(skill_md.with_name("SKILL.md"))
                declared = parse_skill_name(skill_md)
                folder = Path(dest).name
                if declared and declared != folder:
                    raise SyncError(
                        f"{plugin_dir}: SKILL.md declares name {declared!r} but dest folder is {folder!r}"
                    )

            new_hash = content_hash(staged)
            if prev and prev.get("hash") == new_hash and prev.get("ref") == spec["ref"] and target.is_dir() \
                    and content_hash(target) == new_hash:
                log(f"{plugin_dir}: {name} up to date at {spec['ref'][:7]}")
            else:
                # A developer may have committed a local working copy of the skill (for example
                # to develop and test the plugin against it). The registry version replaces it;
                # say so, since a diff here is worth a look in review.
                if target.is_dir() and prev is None:
                    if content_hash(target) == new_hash:
                        log(f"{plugin_dir}: {dest} already matched the registry version")
                    else:
                        notice(
                            f"{plugin_dir}: {dest} contained a local copy that differed from "
                            f"{spec['repo']}@{spec['ref'][:7]}; it has been replaced"
                        )
                if target.exists():
                    shutil.rmtree(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(staged, target, symlinks=False)
                log(f"{plugin_dir}: copied {spec['source']} from {spec['repo']}@{spec['ref'][:7]} to {dest}")
                changed = True

        new_lock["skills"][name] = {
            "repo": spec["repo"],
            "source": spec["source"],
            "dest": dest,
            "ref": spec["ref"],
            "hash": new_hash,
        }

    if MODE == "check":
        if drift:
            for d in drift:
                error(f"{plugin_dir}: {d}")
            return True
        log(f"{plugin_dir}: in sync")
        return False

    # ---- sync mode: persist lock and bump version
    if changed or not lock_path.exists() or lock.get("skills") != new_lock["skills"]:
        write_json(lock_path, new_lock)
        changed = True

    if changed:
        maybe_bump(plugin_dir, manifest_path, manifest)
    elif manifest_dirty:
        write_json(manifest_path, manifest)
        changed = True

    return changed


def maybe_bump(plugin_dir: Path, manifest_path: Path, manifest: dict) -> None:
    """Bump the patch version so harnesses notice that the package content changed."""
    if not BUMP_VERSION:
        return
    current = manifest.get("version")
    if current is None:
        raise SyncError(f"{plugin_dir}: vendored content changed but plugin.json has no version to bump")
    manifest["version"] = bump_patch(current)
    write_json(manifest_path, manifest)
    notice(f"{plugin_dir}: version {current} -> {manifest['version']}")


# ---------------------------------------------------------------- main

def main() -> int:
    if MODE not in ("sync", "check"):
        error(f"unknown mode {MODE!r}; use 'sync' or 'check'")
        return 2

    manifests = sorted(Path(p) for p in glob.glob(PLUGINS_GLOB, recursive=True))
    if not manifests:
        error(f"no plugin.json files matched {PLUGINS_GLOB!r}")
        return 2

    log(f"mode={MODE} namespace={NAMESPACE} plugins={len(manifests)}")
    touched: list[str] = []
    failed = False

    for manifest in manifests:
        try:
            if process_plugin(manifest):
                touched.append(str(manifest.parent))
        except SyncError as exc:
            error(str(exc))
            failed = True
        except (json.JSONDecodeError, OSError) as exc:
            error(f"{manifest}: {exc}")
            failed = True

    set_output("changed", "true" if (MODE == "sync" and touched) else "false")
    set_output("changed_plugins", "\n".join(touched))

    if failed:
        return 1
    if MODE == "check" and touched:
        error(
            f"{len(touched)} plugin(s) are out of sync with their registry references; "
            "run the sync workflow (or the action in sync mode) and commit the result"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())