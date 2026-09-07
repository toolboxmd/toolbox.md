#!/usr/bin/env python3
"""Build and verify the registry-driven toolbox.md public site."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any


# Keep helper imports and CLI exception identity on the same module instance.
if __name__ == "__main__":
    sys.modules["site_pipeline"] = sys.modules[__name__]

REGISTRY_SCHEMA = "https://toolbox.md/schemas/project-registry-v1.schema.json"
RECEIPT_SCHEMA = "https://toolbox.md/schemas/website-parity-receipt-v1.schema.json"
DISCOVERY_SCHEMA = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ROUTE_RE = re.compile(r"^/[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$")
STATIC_ASSETS = ("ascii.json", "hand-left.json", "hand-right.json")
ALLOWED_PROJECT_FIELDS = {
    "id",
    "repository",
    "kind",
    "projectRecordPath",
    "deliveryProfilePath",
    "distribution",
    "website",
    "review",
}
ALLOWED_DISTRIBUTION_FIELDS = {"type", "repository", "recordPath", "catalogPath"}
ALLOWED_WEBSITE_FIELDS = {"repository", "baseUrl", "route"}
ALLOWED_REVIEW_FIELDS = {"reviewedMajor"}


class PipelineError(RuntimeError):
    """A fail-closed website pipeline error."""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PipelineError(f"Missing required JSON file: {path}") from error
    except json.JSONDecodeError as error:
        raise PipelineError(f"Invalid JSON in {path}: {error}") from error


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def require_fields(value: dict[str, Any], required: set[str], label: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise PipelineError(f"{label} is missing required fields: {', '.join(missing)}")


def reject_unknown_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PipelineError(f"{label} has unknown field(s): {', '.join(unknown)}")


def validate_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PipelineError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise PipelineError(f"{label} is unsafe: {value!r}")
    return value


def validate_registry(registry: Any) -> dict[str, Any]:
    if not isinstance(registry, dict):
        raise PipelineError("Project Registry must be an object")
    reject_unknown_fields(registry, {"$schema", "schema", "projects"}, "Project Registry")
    require_fields(registry, {"$schema", "schema", "projects"}, "Project Registry")
    if registry["$schema"] != REGISTRY_SCHEMA or registry["schema"] != 1:
        raise PipelineError("Project Registry schema identity must be v1")
    projects = registry["projects"]
    if not isinstance(projects, list) or not projects:
        raise PipelineError("Project Registry projects must be a non-empty array")

    seen: set[str] = set()
    for position, project in enumerate(projects):
        label = f"Project Registry project #{position + 1}"
        if not isinstance(project, dict):
            raise PipelineError(f"{label} must be an object")
        reject_unknown_fields(project, ALLOWED_PROJECT_FIELDS, label)
        require_fields(project, ALLOWED_PROJECT_FIELDS, label)
        project_id = project["id"]
        if not isinstance(project_id, str) or not ID_RE.fullmatch(project_id):
            raise PipelineError(f"{label} has invalid id")
        if project_id in seen:
            raise PipelineError(f"Project Registry has duplicate id: {project_id}")
        seen.add(project_id)
        if project["kind"] not in {"agent-module", "app"}:
            raise PipelineError(f"{label} has invalid kind")
        if not isinstance(project["repository"], str) or not REPOSITORY_RE.fullmatch(
            project["repository"]
        ):
            raise PipelineError(f"{label} has invalid repository")
        validate_relative_path(project["projectRecordPath"], f"{label}.projectRecordPath")
        validate_relative_path(project["deliveryProfilePath"], f"{label}.deliveryProfilePath")

        distribution = project["distribution"]
        if not isinstance(distribution, dict):
            raise PipelineError(f"{label}.distribution must be an object")
        reject_unknown_fields(distribution, ALLOWED_DISTRIBUTION_FIELDS, f"{label}.distribution")
        require_fields(distribution, {"type", "repository", "recordPath"}, f"{label}.distribution")
        if distribution["type"] not in {"marketplace", "project-release", "project-deployment"}:
            raise PipelineError(f"{label}.distribution has invalid type")
        if not isinstance(distribution["repository"], str) or not REPOSITORY_RE.fullmatch(
            distribution["repository"]
        ):
            raise PipelineError(f"{label}.distribution has invalid repository")
        validate_relative_path(distribution["recordPath"], f"{label}.distribution.recordPath")
        if "catalogPath" in distribution:
            validate_relative_path(distribution["catalogPath"], f"{label}.distribution.catalogPath")
        if distribution["type"] == "marketplace" and "catalogPath" not in distribution:
            raise PipelineError(f"{label}.distribution requires catalogPath for Marketplace")

        website = project["website"]
        if not isinstance(website, dict):
            raise PipelineError(f"{label}.website must be an object")
        reject_unknown_fields(website, ALLOWED_WEBSITE_FIELDS, f"{label}.website")
        require_fields(website, ALLOWED_WEBSITE_FIELDS, f"{label}.website")
        if not isinstance(website["repository"], str) or not REPOSITORY_RE.fullmatch(
            website["repository"]
        ):
            raise PipelineError(f"{label}.website has invalid repository")
        parsed_base = urllib.parse.urlparse(str(website["baseUrl"]))
        if parsed_base.scheme != "https" or not parsed_base.netloc or parsed_base.path not in {"", "/"}:
            raise PipelineError(f"{label}.website.baseUrl must be an HTTPS origin")
        if not isinstance(website["route"], str) or not ROUTE_RE.fullmatch(website["route"]):
            raise PipelineError(f"{label}.website has invalid route")

        review = project["review"]
        if not isinstance(review, dict):
            raise PipelineError(f"{label}.review must be an object")
        reject_unknown_fields(review, ALLOWED_REVIEW_FIELDS, f"{label}.review")
        require_fields(review, ALLOWED_REVIEW_FIELDS, f"{label}.review")
        if not isinstance(review["reviewedMajor"], int) or review["reviewedMajor"] < 0:
            raise PipelineError(f"{label}.review.reviewedMajor must be a non-negative integer")
    return registry


def safe_file(root: Path, relative: str, label: str) -> Path:
    relative = validate_relative_path(relative, label)
    root = root.resolve()
    unresolved = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise PipelineError(f"{label} may not use a symbolic link: {relative}")
    path = unresolved.resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise PipelineError(f"{label} escapes its repository root: {relative}") from error
    if not path.is_file():
        raise PipelineError(f"{label} is missing or is not a regular file: {relative}")
    return path


def run_git(root: Path, *args: str, required: bool = True) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    if required:
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise PipelineError(f"Git verification failed for {root}: {message}")
    return None


def verify_release_checkout(root: Path, tag: str, expected_sha: str | None = None) -> str:
    head = run_git(root, "rev-parse", "HEAD")
    assert head is not None
    tag_sha = run_git(root, "rev-parse", f"{tag}^{{commit}}")
    assert tag_sha is not None
    if head != tag_sha:
        raise PipelineError(f"Released input HEAD {head} does not match {tag} at {tag_sha}")
    if expected_sha is not None and head != expected_sha:
        raise PipelineError(f"Released input HEAD {head} does not match expected source {expected_sha}")
    dirty = run_git(root, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise PipelineError(f"Released input has tracked changes: {root}")
    return head


def parse_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise PipelineError(f"Invalid quoted YAML scalar: {value}") from error
        if not isinstance(parsed, str):
            raise PipelineError("Skill frontmatter scalar must be text")
        return parsed
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_skill_frontmatter(content: str) -> tuple[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PipelineError("Skill file is missing YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as error:
        raise PipelineError("Skill file has unterminated YAML frontmatter") from error

    values: dict[str, str] = {}
    index = 1
    while index < end:
        line = lines[index]
        match = re.match(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$", line)
        if not match:
            index += 1
            continue
        key, raw = match.group(1), match.group(2) or ""
        if raw in {">", ">-", ">+", "|", "|-", "|+"}:
            block: list[str] = []
            index += 1
            while index < end and (lines[index].startswith(" ") or not lines[index].strip()):
                block.append(lines[index].lstrip())
                index += 1
            if raw.startswith(">"):
                values[key] = " ".join(part.strip() for part in block if part.strip())
            else:
                values[key] = "\n".join(block).strip()
            continue
        values[key] = parse_scalar(raw)
        index += 1

    name = values.get("name", "")
    description = values.get("description", "")
    if not ID_RE.fullmatch(name):
        raise PipelineError(f"Skill frontmatter has invalid name: {name!r}")
    if not description or len(description) > 1024:
        raise PipelineError(f"Skill {name} has an invalid description")
    return name, description


def extract_codex_install_commands(
    documents: list[tuple[str, str]],
) -> tuple[str, str]:
    """Return the first shell block in an exact `### Codex` documentation section."""
    for path, content in documents:
        lines = content.splitlines()
        for heading_index, line in enumerate(lines):
            if line.strip() != "### Codex":
                continue
            index = heading_index + 1
            while index < len(lines):
                stripped = lines[index].strip()
                if re.match(r"^#{1,3}(?:\s|$)", stripped):
                    break
                if stripped in {"```sh", "```bash", "```shell"}:
                    end = index + 1
                    while end < len(lines) and lines[end].strip() != "```":
                        end += 1
                    if end == len(lines):
                        raise PipelineError(
                            f"AgentsMD Codex installation block is unterminated in {path}"
                        )
                    commands = "\n".join(lines[index + 1 : end]).strip()
                    if not commands or not any(
                        command.strip().startswith("codex plugin ")
                        for command in commands.splitlines()
                    ):
                        raise PipelineError(
                            f"AgentsMD Codex installation block is invalid in {path}"
                        )
                    return commands, path
                index += 1
    raise PipelineError("AgentsMD documentation has no Codex installation block")


def tree_digest(entries: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item["path"]):
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry["sha256"].encode("ascii"))
        digest.update(b"\0")
        digest.update(entry.get("mode", "").encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def render_source_markdown(content: str) -> str:
    """Render the small released host guide safely, without executing source HTML."""
    def inline(value: str) -> str:
        # Escape first and allow only HTTPS links from the released guide.
        value = html.escape(value)
        value = re.sub(r"\[([^\]]+)\]\((https://[^\s)]+)\)", r'<a href="\2">\1</a>', value)
        value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
        return re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)

    blocks: list[str] = []
    for block in re.split(r"(```[^\n]*\n.*?\n```)", content, flags=re.DOTALL):
        if block.startswith("```"):
            code = block.split("\n", 1)[1].rsplit("\n```", 1)[0]
            blocks.append('<pre class="install"><code>' + html.escape(code) + '</code></pre>')
            continue
        for paragraph in re.split(r"\n\s*\n", block.strip()):
            if not paragraph:
                continue
            blocks.append("<p>" + inline(" ".join(paragraph.splitlines())) + "</p>")
    return "\n".join(blocks)


def render_opencode_section(resolved: dict[str, Any]) -> str:
    content = resolved.get("opencodeDocumentation")
    if content is None:
        return ""
    # The Project Record declaration is the opt-in. The exact released guide
    # owns compatibility, commands, shared Skills, ownership and host limits.
    parts = content.strip().split("\n\n", 2)
    if len(parts) != 3 or parts[0] != "# OpenCode host" or not parts[1].strip():
        raise PipelineError("AgentsMD OpenCode documentation has no compatibility introduction")
    sections = re.split(r"^## (.+)\n", parts[2], flags=re.MULTILINE)
    sections = dict(zip(sections[1::2], sections[2::2]))
    try:
        instructions = sections["Instructions and Skills"].strip().split("\n\n")
        setup = sections["Install, update, status and uninstall"].strip()
        limits = sections["Proof and remaining host differences"].strip().split("\n\n")[-1]
    except KeyError as error:
        raise PipelineError("AgentsMD OpenCode documentation is missing a required section") from error
    setup_blocks = re.split(r"(```sh\n.*?\n```)", setup, maxsplit=1, flags=re.DOTALL)
    if len(instructions) < 2 or len(setup_blocks) != 3 or not limits:
        raise PipelineError("AgentsMD OpenCode documentation is missing setup or host details")
    excerpt = "\n\n".join(instructions + [setup_blocks[0].strip(), setup_blocks[1], limits])
    source_repo = repository_url(resolved["project"]["repository"])
    url = f"{source_repo}/blob/{resolved['sourceSha']}/docs/opencode.md"
    return (
        '<section aria-labelledby="opencode-host"><h2 id="opencode-host">OpenCode CLI</h2>\n'
        + render_source_markdown(parts[1])
        + '<p>This CLI integration is separate from native plugin distribution'
        + (' for ' + html.escape(', '.join(resolved['nativePluginHosts']))
           if resolved['nativePluginHosts'] else '') + '. '
        + '<a href="' + html.escape(url, quote=True)
        + '">Read the exact released OpenCode guide</a>.</p>\n'
        + '<details><summary>Released setup, shared Skills and host limits</summary>\n'
        + render_source_markdown(excerpt)
        + '\n</details></section>'
    )


def deterministic_tar_gz(files: list[tuple[str, bytes, int]]) -> bytes:
    raw = io.BytesIO()
    # The Vercel skills CLI reads the standard ustar name and prefix fields but
    # does not apply PAX path metadata. USTAR therefore makes every published
    # archive directly consumable and fails closed on an unrepresentable path.
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for relative, content, mode in sorted(files, key=lambda item: item[0]):
            info = tarfile.TarInfo(relative)
            info.size = len(content)
            info.mode = mode
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(content))
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0) as output:
        output.write(raw.getvalue())
    return compressed.getvalue()


def normalize_version(value: str, label: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value)
    if not match:
        raise PipelineError(f"{label} is not a plain SemVer version: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def verify_source_entry(
    entry: dict[str, Any], agentsmd_root: Path, plugin_root: Path
) -> tuple[str, str, str, int]:
    require_fields(entry, {"path", "source", "sha256", "mode"}, "Marketplace source file")
    relative_path = validate_relative_path(entry["path"], "Marketplace source file.path")
    source_path = validate_relative_path(entry["source"], "Marketplace source file.source")
    expected_digest = entry["sha256"]
    if not isinstance(expected_digest, str) or not SHA256_RE.fullmatch(expected_digest):
        raise PipelineError(f"Marketplace source file has invalid digest: {relative_path}")
    marketplace_file = safe_file(plugin_root, relative_path, "Marketplace bundle file")
    source_file = safe_file(agentsmd_root, source_path, "AgentsMD source file")
    if sha256_file(marketplace_file) != expected_digest:
        raise PipelineError(f"Marketplace bundle digest mismatch: {relative_path}")
    if sha256_file(source_file) != expected_digest:
        raise PipelineError(f"AgentsMD source digest mismatch: {source_path}")
    mode_text = str(entry["mode"])
    if mode_text not in {"100644", "100755"}:
        raise PipelineError(f"Marketplace source file has unsupported mode: {relative_path}")
    return relative_path, source_path, expected_digest, 0o755 if mode_text == "100755" else 0o644


def resolve_agentsmd(
    project: dict[str, Any], agentsmd_root: Path, marketplace_root: Path
) -> dict[str, Any]:
    distribution = project["distribution"]
    source_path = safe_file(
        marketplace_root, distribution["recordPath"], "Marketplace source record"
    )
    source = read_json(source_path)
    if not isinstance(source, dict):
        raise PipelineError("Marketplace source record must be an object")
    require_fields(
        source,
        {"schema", "project", "release", "commit", "projectRecord", "files"},
        "Marketplace source record",
    )
    if source["schema"] != 1 or source["project"] != project["id"]:
        raise PipelineError("Marketplace source record identity does not match Project Registry")
    if not isinstance(source["commit"], str) or not SHA_RE.fullmatch(source["commit"]):
        raise PipelineError("Marketplace source record has invalid source commit")
    if not isinstance(source["release"], str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", source["release"]):
        raise PipelineError("Marketplace source record has invalid release")

    project_record_path = safe_file(
        agentsmd_root, project["projectRecordPath"], "AgentsMD Project Record"
    )
    project_record_digest = sha256_file(project_record_path)
    source_record = source["projectRecord"]
    if not isinstance(source_record, dict):
        raise PipelineError("Marketplace Project Record evidence must be an object")
    if source_record.get("path") != project["projectRecordPath"]:
        raise PipelineError("Marketplace Project Record path does not match Project Registry")
    if source_record.get("sha256") != project_record_digest:
        raise PipelineError("Project Record digest does not match Marketplace evidence")

    project_record = read_json(project_record_path)
    if not isinstance(project_record, dict):
        raise PipelineError("AgentsMD Project Record must be an object")
    require_fields(project_record, {"id", "kind", "outcome", "factSources"}, "AgentsMD Project Record")
    if project_record["id"] != project["id"] or project_record["kind"] != project["kind"]:
        raise PipelineError("AgentsMD Project Record identity does not match Project Registry")
    facts = project_record["factSources"]
    if not isinstance(facts, dict):
        raise PipelineError("AgentsMD Project Record factSources must be an object")
    require_fields(facts, {"version", "delivery", "documentation"}, "AgentsMD factSources")

    version_path = safe_file(agentsmd_root, facts["version"], "AgentsMD version source")
    version = version_path.read_text(encoding="utf-8").strip()
    major, _, _ = normalize_version(version, "AgentsMD version")
    if source["release"] != f"v{version}":
        raise PipelineError("AgentsMD release does not match its version source")
    if major != project["review"]["reviewedMajor"]:
        raise PipelineError(
            f"AgentsMD v{version} requires a complete website review; "
            f"Project Registry currently approves major {project['review']['reviewedMajor']}"
        )

    marketplace_version_path = safe_file(marketplace_root, "VERSION", "Marketplace version source")
    marketplace_version = marketplace_version_path.read_text(encoding="utf-8").strip()
    normalize_version(marketplace_version, "Marketplace version")
    marketplace_sha = verify_release_checkout(
        marketplace_root, f"v{marketplace_version}"
    )
    agentsmd_sha = verify_release_checkout(
        agentsmd_root, source["release"], expected_sha=source["commit"]
    )

    catalog_path = safe_file(marketplace_root, distribution["catalogPath"], "Marketplace catalog")
    catalog = read_json(catalog_path)
    plugins = catalog.get("plugins") if isinstance(catalog, dict) else None
    if not isinstance(plugins, list):
        raise PipelineError("Marketplace catalog plugins must be an array")
    matches = [item for item in plugins if isinstance(item, dict) and item.get("name") == project["id"]]
    if len(matches) != 1:
        raise PipelineError("Marketplace catalog must contain exactly one AgentsMD entry")
    catalog_entry = matches[0]
    expected_catalog = {
        "github": project["repository"],
        "release": source["release"],
        "sha": source["commit"],
        "kind": project["kind"],
    }
    for key, expected in expected_catalog.items():
        if catalog_entry.get(key) != expected:
            raise PipelineError(f"Marketplace catalog {key} does not match released source")
    if catalog_entry.get("projectRecord") != source_record:
        raise PipelineError("Marketplace catalog Project Record evidence does not match SOURCE.json")

    delivery_path = safe_file(
        agentsmd_root, project["deliveryProfilePath"], "AgentsMD delivery profile"
    )
    delivery = read_json(delivery_path)
    if not isinstance(delivery, dict) or delivery.get("website") != project["website"]:
        raise PipelineError("AgentsMD delivery profile website mapping does not match Project Registry")

    delivery_sources = facts["delivery"]
    if not isinstance(delivery_sources, dict) or not delivery_sources:
        raise PipelineError("AgentsMD delivery sources must be a non-empty object")
    for host, relative in delivery_sources.items():
        manifest = read_json(safe_file(agentsmd_root, relative, f"AgentsMD {host} manifest"))
        if not isinstance(manifest, dict) or manifest.get("version") != version:
            raise PipelineError(f"AgentsMD {host} manifest version does not match VERSION")

    documentation_paths = facts["documentation"]
    if not isinstance(documentation_paths, list) or not documentation_paths:
        raise PipelineError("AgentsMD documentation sources must be a non-empty array")
    documentation: list[tuple[str, str]] = []
    for relative in documentation_paths:
        path = safe_file(agentsmd_root, relative, "AgentsMD documentation source")
        documentation.append((relative, path.read_text(encoding="utf-8")))
    codex_install_commands, codex_install_source = extract_codex_install_commands(
        documentation
    )

    raw_entries = source["files"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise PipelineError("Marketplace source files must be a non-empty array")
    plugin_root = source_path.parent
    verified_entries: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_sources: set[str] = set()
    source_lookup: dict[str, dict[str, Any]] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise PipelineError("Marketplace source file entry must be an object")
        relative, source_relative, digest, mode = verify_source_entry(
            raw_entry, agentsmd_root, plugin_root
        )
        if relative in seen_paths or source_relative in seen_sources:
            raise PipelineError(f"Marketplace source record contains duplicate identity: {relative}")
        seen_paths.add(relative)
        seen_sources.add(source_relative)
        normalized = {
            "path": relative,
            "source": source_relative,
            "sha256": digest,
            "mode": str(raw_entry["mode"]),
            "numericMode": mode,
        }
        verified_entries.append(normalized)
        source_lookup[source_relative] = normalized

    # Marketplace packages the files needed by its provider adapter. Host-specific
    # delivery manifests remain source-owned and are validated at the exact source
    # commit, but they are not required to be copied into every provider bundle.
    for required_source in [facts["version"], project["projectRecordPath"]]:
        if required_source not in source_lookup:
            raise PipelineError(f"Marketplace bundle omits required source: {required_source}")

    skill_paths = facts.get("skills")
    if not isinstance(skill_paths, list) or not skill_paths:
        raise PipelineError("AgentsMD Project Record must name at least one active Skill")
    skills: list[dict[str, Any]] = []
    seen_skills: set[str] = set()
    shared_legal = [
        entry
        for entry in verified_entries
        if entry["source"] in {"LICENSE", "THIRD_PARTY_NOTICES.md"}
        or entry["source"].startswith("LICENSES/")
    ]
    for skill_path in skill_paths:
        skill_file = safe_file(agentsmd_root, skill_path, "AgentsMD Skill source")
        if skill_path not in source_lookup:
            raise PipelineError(f"Marketplace bundle omits active Skill: {skill_path}")
        skill_name, description = parse_skill_frontmatter(skill_file.read_text(encoding="utf-8"))
        if skill_name in seen_skills:
            raise PipelineError(f"AgentsMD Project Record contains duplicate Skill: {skill_name}")
        seen_skills.add(skill_name)
        skill_directory = str(Path(skill_path).parent)
        packaged = [
            entry
            for entry in verified_entries
            if entry["source"] == skill_directory
            or entry["source"].startswith(skill_directory + "/")
        ]
        if not packaged:
            raise PipelineError(f"Marketplace bundle contains no files for Skill: {skill_name}")
        files: list[tuple[str, bytes, int]] = []
        for entry in packaged:
            relative_inside_skill = str(Path(entry["source"]).relative_to(skill_directory))
            files.append(
                (
                    relative_inside_skill,
                    safe_file(agentsmd_root, entry["source"], "AgentsMD Skill artifact").read_bytes(),
                    entry["numericMode"],
                )
            )
        existing_names = {relative for relative, _, _ in files}
        for entry in shared_legal:
            if entry["source"] in existing_names:
                continue
            files.append(
                (
                    entry["source"],
                    safe_file(
                        agentsmd_root, entry["source"], "AgentsMD legal artifact"
                    ).read_bytes(),
                    entry["numericMode"],
                )
            )
        if "SKILL.md" not in {relative for relative, _, _ in files}:
            raise PipelineError(f"Marketplace bundle omits SKILL.md for Skill: {skill_name}")
        skills.append(
            {
                "name": skill_name,
                "description": description,
                "sourcePath": skill_path,
                "files": files,
            }
        )
    skills.sort(key=lambda item: item["name"])

    return {
        "project": project,
        "projectRecord": project_record,
        "projectRecordDigest": project_record_digest,
        "version": version,
        "release": source["release"],
        "sourceSha": agentsmd_sha,
        "marketplaceVersion": marketplace_version,
        "marketplaceRelease": f"v{marketplace_version}",
        "marketplaceSha": marketplace_sha,
        "bundleDigest": tree_digest(verified_entries),
        "documentationPaths": documentation_paths,
        "codexInstallCommands": codex_install_commands,
        "codexInstallSource": codex_install_source,
        "opencodeDocumentation": dict(documentation).get("docs/opencode.md"),
        "nativePluginHosts": [
            {"codex": "Codex", "claude-code": "Claude Code", "grok-build": "Grok"}.get(host, host)
            for host in facts["delivery"]
        ],
        "skills": skills,
    }


def repository_url(repository: str) -> str:
    return f"https://github.com/{repository}"


def build_receipt(resolved: dict[str, Any], toolbox_sha: str) -> dict[str, Any]:
    project = resolved["project"]
    base_url = project["website"]["baseUrl"].rstrip("/")
    canonical = base_url + project["website"]["route"]
    source_repo = repository_url(project["repository"])
    marketplace_repo = repository_url(project["distribution"]["repository"])
    documentation = [
        {
            "path": path,
            "url": f"{source_repo}/blob/{resolved['sourceSha']}/{urllib.parse.quote(path)}",
        }
        for path in resolved["documentationPaths"]
    ]
    return {
        "$schema": RECEIPT_SCHEMA,
        "schema": 1,
        "state": "candidate",
        "project": {
            "id": project["id"],
            "kind": project["kind"],
            "repository": project["repository"],
            "version": resolved["version"],
            "release": resolved["release"],
            "sourceSha": resolved["sourceSha"],
            "projectRecordSha256": resolved["projectRecordDigest"],
        },
        "distribution": {
            "type": project["distribution"]["type"],
            "repository": project["distribution"]["repository"],
            "version": resolved["marketplaceVersion"],
            "release": resolved["marketplaceRelease"],
            "sourceSha": resolved["marketplaceSha"],
            "artifact": {
                "algorithm": "sha256-tree-v1",
                "sha256": resolved["bundleDigest"],
                "scope": f"plugins/{project['id']} listed files",
            },
        },
        "website": {
            "repository": project["website"]["repository"],
            "sourceSha": toolbox_sha,
            "baseUrl": base_url,
            "route": project["website"]["route"],
            "canonicalUrl": canonical,
            "manifestSha256": None,
        },
        "documentation": {"state": "released", "sources": documentation},
        "publicLinks": {
            "project": source_repo,
            "projectRelease": f"{source_repo}/releases/tag/{resolved['release']}",
            "distribution": marketplace_repo,
            "distributionRelease": f"{marketplace_repo}/releases/tag/{resolved['marketplaceRelease']}",
            "website": canonical,
        },
        "discovery": {
            "llmsTxt": f"{base_url}/llms.txt",
            "skillsIndex": f"{base_url}/.well-known/agent-skills/index.json",
            "compatibility": "Vercel skills CLI and Cloudflare discovery RFC v0.2.0",
            "coreAgentSkillsSpecificationClaimed": False,
            "skillCount": len(resolved["skills"]),
        },
        "websiteDeployment": {
            "provider": "cloudflare-pages",
            "project": "toolbox-md",
            "state": "candidate",
            "deploymentUrl": None,
        },
        "liveVerification": {
            "state": "pending",
            "observedAt": None,
            "runUrl": None,
            "checks": [],
        },
        "seoImpact": {
            "scope": "v1 metadata and indexability",
            "fullProgram": "deferred",
            "changes": [
                "canonical URL",
                "page title and description",
                "SoftwareApplication JSON-LD",
                "robots.txt",
                "sitemap.xml",
            ],
        },
    }


def render_agentsmd_page(resolved: dict[str, Any], template_path: Path) -> str:
    project = resolved["project"]
    base_url = project["website"]["baseUrl"].rstrip("/")
    canonical = base_url + project["website"]["route"]
    source_repo = repository_url(project["repository"])
    marketplace_repo = repository_url(project["distribution"]["repository"])
    cards: list[str] = []
    for skill in resolved["skills"]:
        source_url = f"{source_repo}/blob/{resolved['sourceSha']}/{skill['sourcePath']}"
        cards.append(
            '<li class="skill"><a href="{url}"><code>{name}</code></a>'
            '<span>{description}</span></li>'.format(
                url=html.escape(source_url, quote=True),
                name=html.escape(skill["name"]),
                description=html.escape(skill["description"]),
            )
        )
    structured_data = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": "AgentsMD",
            "applicationCategory": "DeveloperApplication",
            "softwareVersion": resolved["version"],
            "description": resolved["projectRecord"]["outcome"],
            "url": canonical,
            "codeRepository": source_repo,
            "license": f"{source_repo}/blob/{resolved['sourceSha']}/LICENSE",
        },
        separators=(",", ":"),
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        canonical=html.escape(canonical, quote=True),
        description=html.escape(resolved["projectRecord"]["outcome"], quote=True),
        version=html.escape(resolved["version"]),
        release=html.escape(resolved["release"]),
        source_sha=html.escape(resolved["sourceSha"]),
        short_sha=html.escape(resolved["sourceSha"][:12]),
        outcome=html.escape(resolved["projectRecord"]["outcome"]),
        project_record_digest=html.escape(resolved["projectRecordDigest"]),
        marketplace_version=html.escape(resolved["marketplaceVersion"]),
        marketplace_sha=html.escape(resolved["marketplaceSha"]),
        skill_count=str(len(resolved["skills"])),
        skill_cards="\n".join(cards),
        install_commands=html.escape(resolved["codexInstallCommands"]),
        opencode_section=render_opencode_section(resolved),
        installation_url=html.escape(
            f"{source_repo}/blob/{resolved['sourceSha']}/"
            f"{urllib.parse.quote(resolved['codexInstallSource'])}",
            quote=True,
        ),
        source_url=html.escape(source_repo, quote=True),
        release_url=html.escape(f"{source_repo}/releases/tag/{resolved['release']}", quote=True),
        marketplace_url=html.escape(marketplace_repo, quote=True),
        marketplace_release_url=html.escape(
            f"{marketplace_repo}/releases/tag/{resolved['marketplaceRelease']}", quote=True
        ),
        discovery_url=html.escape(
            f"{base_url}/.well-known/agent-skills/index.json", quote=True
        ),
        structured_data=structured_data,
    )


def render_homepage(resolved: dict[str, Any], template_path: Path) -> str:
    rendered = template_path.read_text(encoding="utf-8")
    replacements = {
        "@@AGENTSMD_ROUTE@@": html.escape(
            resolved["project"]["website"]["route"], quote=True
        ),
        "@@AGENTSMD_VERSION@@": html.escape(resolved["version"]),
        "@@AGENTSMD_OUTCOME@@": html.escape(resolved["projectRecord"]["outcome"]),
    }
    for marker, value in replacements.items():
        if rendered.count(marker) != 1:
            raise PipelineError(f"Homepage template must contain exactly one {marker}")
        rendered = rendered.replace(marker, value)
    return rendered


def render_llms_txt(resolved: dict[str, Any]) -> str:
    project = resolved["project"]
    base_url = project["website"]["baseUrl"].rstrip("/")
    source_repo = repository_url(project["repository"])
    marketplace_repo = repository_url(project["distribution"]["repository"])
    skills = "\n".join(
        f"- {skill['name']}: {skill['description']}" for skill in resolved["skills"]
    )
    return f"""# toolbox.md

> The ToolboxMD Project directory for people and agents.

## AgentsMD v{resolved['version']}

{resolved['projectRecord']['outcome']}

- Canonical page: {base_url}{project['website']['route']}
- Project release: {source_repo}/releases/tag/{resolved['release']}
- Source commit: {resolved['sourceSha']}
- Marketplace release: {marketplace_repo}/releases/tag/{resolved['marketplaceRelease']}
- Project Record SHA-256: {resolved['projectRecordDigest']}

## Active AgentsMD Skills

{skills}

## Machine discovery

- Vercel skills CLI compatible index: {base_url}/.well-known/agent-skills/index.json
- The well-known index is a distribution compatibility endpoint. It is not claimed as part of the core Agent Skills file-format specification.

## Source

- ToolboxMD: https://github.com/toolboxmd
- AgentsMD: {source_repo}
"""


def write_discovery(output: Path, resolved: dict[str, Any]) -> dict[str, Any]:
    discovery_root = output / ".well-known/agent-skills"
    discovery_root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for skill in resolved["skills"]:
        files = skill["files"]
        if len(files) == 1 and files[0][0] == "SKILL.md":
            content = files[0][1]
            digest = sha256_bytes(content)
            relative = f".well-known/agent-skills/{skill['name']}/{digest}/SKILL.md"
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            artifact_type = "skill-md"
        else:
            content = deterministic_tar_gz(files)
            digest = sha256_bytes(content)
            relative = f".well-known/agent-skills/{skill['name']}/{digest}/{skill['name']}.tar.gz"
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            artifact_type = "archive"
        entries.append(
            {
                "name": skill["name"],
                "type": artifact_type,
                "description": skill["description"],
                "url": "/" + relative,
                "digest": f"sha256:{digest}",
            }
        )
    index = {"$schema": DISCOVERY_SCHEMA, "skills": entries}
    write_json(discovery_root / "index.json", index)
    return index


def write_headers(output: Path) -> None:
    (output / "_headers").write_text(
        """/.well-known/agent-skills/*
  Access-Control-Allow-Origin: *
  Cache-Control: public, max-age=300
  X-Content-Type-Options: nosniff

/site-manifest.json
  Cache-Control: no-cache
  X-Content-Type-Options: nosniff

/llms.txt
  Cache-Control: public, max-age=300
  X-Content-Type-Options: nosniff
""",
        encoding="utf-8",
    )


def site_files(output: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.name == "site-manifest.json":
            continue
        relative = "/" + path.relative_to(output).as_posix()
        result[relative] = sha256_file(path)
    return result


def write_site_manifest(output: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    manifest = {
        "schema": 1,
        "toolbox": {
            "repository": receipt["website"]["repository"],
            "sourceSha": receipt["website"]["sourceSha"],
        },
        "projects": [
            {
                "id": receipt["project"]["id"],
                "version": receipt["project"]["version"],
                "sourceSha": receipt["project"]["sourceSha"],
                "projectRecordSha256": receipt["project"]["projectRecordSha256"],
                "distributionSha": receipt["distribution"]["sourceSha"],
                "distributionArtifactSha256": receipt["distribution"]["artifact"]["sha256"],
            }
        ],
        "files": site_files(output),
    }
    for app in receipt.get("apps", []):
        manifest["projects"].append({"id": app["project"], **app["identity"]})
    write_json(output / "site-manifest.json", manifest)
    receipt["website"]["manifestSha256"] = sha256_file(output / "site-manifest.json")
    return manifest


def validate_output(output: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    required = [
        "index.html",
        "agentsmd/index.html",
        "llms.txt",
        ".well-known/agent-skills/index.json",
        "site-manifest.json",
        "robots.txt",
        "sitemap.xml",
        "_headers",
        "schemas/project-registry-v1.schema.json",
        "schemas/website-parity-receipt-v1.schema.json",
    ]
    for relative in required:
        safe_file(output, relative, "Generated website file")

    page = (output / "agentsmd/index.html").read_text(encoding="utf-8")
    expected_page_values = [
        f"AgentsMD v{receipt['project']['version']}",
        receipt["project"]["sourceSha"],
        receipt["project"]["projectRecordSha256"],
        f'rel="canonical" href="{receipt["website"]["canonicalUrl"]}"',
    ]
    for expected in expected_page_values:
        if expected not in page:
            raise PipelineError(f"Generated AgentsMD page omits required identity: {expected}")
    if "—" in page:
        raise PipelineError("Generated AgentsMD page contains an em dash")

    homepage = (output / "index.html").read_text(encoding="utf-8")
    for expected in (
        f"v{receipt['project']['version']}",
        receipt["website"]["route"],
    ):
        if expected not in homepage:
            raise PipelineError(f"Generated homepage omits released Project identity: {expected}")
    if "@@AGENTSMD_" in homepage:
        raise PipelineError("Generated homepage contains an unresolved Project marker")

    llms = (output / "llms.txt").read_text(encoding="utf-8")
    if "<html" in llms.lower() or receipt["project"]["sourceSha"] not in llms:
        raise PipelineError("Generated llms.txt is not a real exact-identity document")

    discovery = read_json(output / ".well-known/agent-skills/index.json")
    if not isinstance(discovery, dict) or discovery.get("$schema") != DISCOVERY_SCHEMA:
        raise PipelineError("Generated Skill discovery index has the wrong compatibility schema")
    skills = discovery.get("skills")
    if not isinstance(skills, list) or len(skills) != receipt["discovery"]["skillCount"]:
        raise PipelineError("Generated Skill discovery index has the wrong Skill count")
    names: set[str] = set()
    for skill in skills:
        if not isinstance(skill, dict):
            raise PipelineError("Generated Skill discovery entry must be an object")
        require_fields(skill, {"name", "type", "description", "url", "digest"}, "Skill entry")
        if set(skill) != {"name", "type", "description", "url", "digest"}:
            raise PipelineError("Generated Skill discovery entry has unsupported fields")
        if not ID_RE.fullmatch(str(skill["name"])) or skill["name"] in names:
            raise PipelineError("Generated Skill discovery index has an invalid or duplicate name")
        names.add(skill["name"])
        if skill["type"] not in {"skill-md", "archive"}:
            raise PipelineError(f"Generated Skill {skill['name']} has invalid type")
        if not isinstance(skill["description"], str) or not skill["description"]:
            raise PipelineError(f"Generated Skill {skill['name']} has invalid description")
        if not isinstance(skill["url"], str) or not skill["url"].startswith(
            "/.well-known/agent-skills/"
        ):
            raise PipelineError(f"Generated Skill {skill['name']} has invalid URL")
        artifact = safe_file(output, skill["url"].lstrip("/"), "Generated Skill artifact")
        if skill["digest"] != f"sha256:{sha256_file(artifact)}":
            raise PipelineError(f"Generated Skill {skill['name']} has invalid digest")

    manifest_path = output / "site-manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise PipelineError("Generated site manifest is invalid")
    projects = manifest.get("projects")
    if not isinstance(projects, list) or any(not isinstance(entry, dict) for entry in projects):
        raise PipelineError("Generated site manifest Agent entries must be objects in a list")
    agent_entries = [entry for entry in projects if entry.get("id") == receipt["project"]["id"]]
    if len(agent_entries) != 1:
        raise PipelineError("Generated site manifest must contain exactly one required Agent entry")
    expected_agent = {
        "id": receipt["project"]["id"],
        "version": receipt["project"]["version"],
        "sourceSha": receipt["project"]["sourceSha"],
        "projectRecordSha256": receipt["project"]["projectRecordSha256"],
        "distributionSha": receipt["distribution"]["sourceSha"],
        "distributionArtifactSha256": receipt["distribution"]["artifact"]["sha256"],
    }
    for field, expected in expected_agent.items():
        if not isinstance(agent_entries[0].get(field), str) or agent_entries[0][field] != expected:
            raise PipelineError(f"Generated site manifest Agent identity mismatch: {field}")
    toolbox = manifest.get("toolbox")
    if not isinstance(toolbox, dict):
        raise PipelineError("Generated site manifest toolbox identity must be an object")
    for field in ("repository", "sourceSha"):
        if not isinstance(toolbox.get(field), str) or toolbox[field] != receipt["website"][field]:
            raise PipelineError(f"Generated site manifest toolbox identity mismatch: {field}")
    expected_files = site_files(output)
    if manifest.get("files") != expected_files:
        raise PipelineError("Generated site manifest does not match website files")
    if receipt["website"]["manifestSha256"] != sha256_file(manifest_path):
        raise PipelineError("Website Parity Receipt manifest digest is invalid")

    urls: list[str] = list(receipt["publicLinks"].values())
    urls.extend(source["url"] for source in receipt["documentation"]["sources"])
    urls.extend([receipt["discovery"]["llmsTxt"], receipt["discovery"]["skillsIndex"]])
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise PipelineError(f"Website Parity Receipt contains a non-HTTPS public link: {url}")
    for app in receipt.get("apps", []):
        import gitpix_pipeline
        gitpix_pipeline.validate_public(output, app)
        if {"id": app["project"], **app["identity"]} not in manifest["projects"]:
            raise PipelineError("GitPix whole-site manifest identity mismatch")
    return {"files": len(expected_files), "skills": len(skills), "state": "valid"}


def promote_candidate(candidate: Path, output: Path) -> None:
    output = output.resolve()
    if output == Path("/") or output == output.parent:
        raise PipelineError(f"Refusing unsafe website output path: {output}")
    if output.exists() and output.is_symlink():
        raise PipelineError(f"Refusing symlink website output path: {output}")
    backup = output.parent / f".{output.name}.last-known-good-{os.getpid()}"
    if backup.exists():
        raise PipelineError(f"Unexpected existing website backup path: {backup}")
    had_output = output.exists()
    if had_output:
        output.rename(backup)
    try:
        candidate.rename(output)
    except Exception:
        if had_output and backup.exists() and not output.exists():
            backup.rename(output)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def build_site(
    project_root: Path,
    agentsmd_root: Path,
    marketplace_root: Path,
    output_root: Path,
    gitpix_root: Path | None = None,
    gitpix_inputs: Path | None = None,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    agentsmd_root = agentsmd_root.resolve()
    marketplace_root = marketplace_root.resolve()
    output_root = output_root.resolve()
    registry = validate_registry(read_json(project_root / ".toolboxmd/projects.json"))
    projects = [project for project in registry["projects"] if project["id"] == "agentsmd"]
    if len(projects) != 1:
        raise PipelineError("Project Registry must contain exactly one AgentsMD mapping")
    resolved = resolve_agentsmd(projects[0], agentsmd_root, marketplace_root)
    toolbox_sha = run_git(project_root, "rev-parse", "HEAD")
    assert toolbox_sha is not None
    receipt = build_receipt(resolved, toolbox_sha)
    apps = [p for p in registry["projects"] if p["id"] == "gitpix"]
    gitpix = None
    if apps:
        if gitpix_root is None or gitpix_inputs is None:
            raise PipelineError("GitPix mapping requires exact released source and verified activation inputs; publication preserved")
        import gitpix_pipeline
        gitpix = gitpix_pipeline.resolve(apps[0], gitpix_root.resolve(), read_json(gitpix_inputs))

    output_root.parent.mkdir(parents=True, exist_ok=True)
    candidate = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}.candidate-", dir=str(output_root.parent))
    )
    try:
        homepage = candidate / "index.html"
        homepage.write_text(
            render_homepage(resolved, project_root / "index.html"),
            encoding="utf-8",
        )
        for relative in STATIC_ASSETS:
            source = safe_file(project_root, relative, "Toolbox static source")
            destination = candidate / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        for relative in (
            "schemas/project-registry-v1.schema.json",
            "schemas/website-parity-receipt-v1.schema.json",
        ):
            source = safe_file(project_root, relative, "Toolbox schema source")
            destination = candidate / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

        page_path = candidate / "agentsmd/index.html"
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(
            render_agentsmd_page(resolved, project_root / "templates/agentsmd.html"),
            encoding="utf-8",
        )
        (candidate / "llms.txt").write_text(render_llms_txt(resolved), encoding="utf-8")
        write_discovery(candidate, resolved)
        write_headers(candidate)
        (candidate / "robots.txt").write_text(
            "User-agent: *\nAllow: /\nSitemap: https://toolbox.md/sitemap.xml\n",
            encoding="utf-8",
        )
        (candidate / "sitemap.xml").write_text(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
            "  <url><loc>https://toolbox.md/</loc></url>\n"
            "  <url><loc>https://toolbox.md/agentsmd</loc></url>\n"
            "</urlset>\n",
            encoding="utf-8",
        )
        if gitpix is not None:
            evidence_path = project_root / ".toolboxmd/evidence/gitpix-pilot.json"
            evidence = read_json(evidence_path) if evidence_path.exists() else None
            receipt["apps"] = [gitpix_pipeline.render(candidate, project_root, gitpix, evidence)]
        write_site_manifest(candidate, receipt)
        validate_output(candidate, receipt)
        promote_candidate(candidate, output_root)
    except Exception:
        if candidate.exists():
            shutil.rmtree(candidate)
        raise
    return receipt


def fetch_url(url: str, timeout: float = 20.0) -> tuple[bytes, str, str]:
    headers = {
        "Accept": "*/*",
        "Cache-Control": "no-cache",
        "User-Agent": "toolboxmd-website-parity/1",
    }
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.read(),
                response.headers.get_content_type(),
                response.geturl(),
            )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        raise PipelineError(f"Could not fetch {url}: {error}") from error


def live_status(output: Path, base_url: str) -> dict[str, Any]:
    expected_path = safe_file(output, "site-manifest.json", "Expected site manifest")
    expected = read_json(expected_path)
    cache_key = sha256_file(expected_path)[:16]
    url = f"{base_url.rstrip('/')}/site-manifest.json?expected={cache_key}"
    try:
        body, content_type, _ = fetch_url(url)
        if content_type != "application/json":
            return {"state": "stale", "stale": True, "reason": f"manifest content type is {content_type}"}
        observed = json.loads(body)
    except (PipelineError, json.JSONDecodeError) as error:
        return {"state": "stale", "stale": True, "reason": str(error)}
    if observed != expected:
        return {"state": "stale", "stale": True, "reason": "live manifest differs"}
    return {"state": "current", "stale": False, "reason": "live manifest matches"}


def verify_live(
    output: Path,
    candidate_receipt: dict[str, Any],
    base_url: str,
    deployment_url: str | None,
    run_url: str | None,
    attempts: int,
    delay: float,
) -> dict[str, Any]:
    if attempts < 1 or delay < 0:
        raise PipelineError("Live Verification attempts must be positive and delay non-negative")
    status: dict[str, Any] = {}
    for attempt in range(attempts):
        status = live_status(output, base_url)
        if not status["stale"]:
            break
        if attempt + 1 < attempts:
            time.sleep(delay)
    if status.get("stale", True):
        raise PipelineError(f"Live website did not reach expected manifest: {status.get('reason')}")

    checks: list[dict[str, Any]] = []
    endpoints = [
        ("homepage", "/", "index.html", "text/html"),
        ("canonical page", "/agentsmd", "agentsmd/index.html", "text/html"),
        ("llms.txt", "/llms.txt", "llms.txt", "text/plain"),
        (
            "Skill discovery",
            "/.well-known/agent-skills/index.json",
            ".well-known/agent-skills/index.json",
            "application/json",
        ),
        ("site manifest", "/site-manifest.json", "site-manifest.json", "application/json"),
        ("robots.txt", "/robots.txt", "robots.txt", "text/plain"),
        ("sitemap.xml", "/sitemap.xml", "sitemap.xml", "application/xml"),
    ]
    if candidate_receipt.get("apps"):
        endpoints.extend([
            ("GitPix canonical page", "/gitpix", "gitpix/index.html", "text/html"),
            ("GitPix OpenAPI", "/gitpix/openapi.json", "gitpix/openapi.json", "application/json"),
            ("GitPix API prose", "/gitpix/llms.txt", "gitpix/llms.txt", "text/plain"),
            ("GitPix release identity", "/gitpix/release.json", "gitpix/release.json", "application/json"),
            ("GitPix parity receipt", "/gitpix/parity.json", "gitpix/parity.json", "application/json"),
        ])
    fetched: dict[str, bytes] = {}
    for name, route, local_path, expected_type in endpoints:
        body, content_type, final_url = fetch_url(base_url.rstrip("/") + route)
        if content_type != expected_type:
            raise PipelineError(
                f"Live {name} content type is {content_type}, expected {expected_type}"
            )
        expected_digest = sha256_file(safe_file(output, local_path, f"Expected live {name}"))
        observed_digest = sha256_bytes(body)
        if observed_digest != expected_digest:
            raise PipelineError(f"Live {name} bytes do not match the Website Candidate")
        fetched[route] = body
        checks.append(
            {
                "name": name,
                "url": final_url,
                "contentType": content_type,
                "sha256": observed_digest,
                "state": "passed",
            }
        )

    page = fetched["/agentsmd"].decode("utf-8")
    if candidate_receipt["project"]["version"] not in page or candidate_receipt["project"][
        "sourceSha"
    ] not in page:
        raise PipelineError("Live AgentsMD page does not expose exact release identity")
    llms = fetched["/llms.txt"].decode("utf-8")
    if "<html" in llms.lower() or candidate_receipt["project"]["sourceSha"] not in llms:
        raise PipelineError("Live llms.txt is missing or is an HTML fallback")
    discovery = json.loads(fetched["/.well-known/agent-skills/index.json"])
    if discovery.get("$schema") != DISCOVERY_SCHEMA:
        raise PipelineError("Live Skill discovery index is not Vercel skills CLI compatible")
    for skill in discovery.get("skills", []):
        artifact_url = urllib.parse.urljoin(base_url.rstrip("/") + "/", skill["url"])
        body, _, final_url = fetch_url(artifact_url)
        if skill["digest"] != f"sha256:{sha256_bytes(body)}":
            raise PipelineError(f"Live Skill artifact digest mismatch: {skill['name']}")
        checks.append(
            {
                "name": f"Skill artifact {skill['name']}",
                "url": final_url,
                "sha256": sha256_bytes(body),
                "state": "passed",
            }
        )

    linked_sources = [
        (f"public link {name}", url)
        for name, url in candidate_receipt["publicLinks"].items()
    ]
    linked_sources.extend(
        (f"documentation {source['path']}", source["url"])
        for source in candidate_receipt["documentation"]["sources"]
    )
    for name, url in linked_sources:
        body, content_type, final_url = fetch_url(url)
        if not body:
            raise PipelineError(f"Public link returned an empty response: {url}")
        if urllib.parse.urlparse(final_url).scheme != "https":
            raise PipelineError(f"Public link redirected outside HTTPS: {url}")
        checks.append(
            {
                "name": name,
                "url": final_url,
                "contentType": content_type,
                "state": "passed",
            }
        )

    receipt = json.loads(json.dumps(candidate_receipt))
    receipt["state"] = "live-verified"
    receipt["websiteDeployment"].update(
        {
            "state": "deployed" if deployment_url else "current",
            "deploymentUrl": deployment_url or None,
        }
    )
    receipt["liveVerification"] = {
        "state": "passed",
        "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "runUrl": run_url or None,
        "checks": checks,
    }
    for app in receipt.get("apps", []):
        app["states"]["websitePublication"] = {"state": receipt["websiteDeployment"]["state"], "deploymentUrl": deployment_url or None}
        app["states"]["websiteVerification"] = {"state": "passed", "observedAt": receipt["liveVerification"]["observedAt"], "runUrl": run_url or None}
    return receipt


def write_github_output(path: Path, values: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            if isinstance(value, bool):
                value = str(value).lower()
            output.write(f"{key}={value}\n")


def released_source_hint(marketplace_root: Path, source_record: str) -> dict[str, str]:
    source = read_json(
        safe_file(marketplace_root, source_record, "Marketplace source record hint")
    )
    if not isinstance(source, dict):
        raise PipelineError("Marketplace source record hint must be an object")
    release = source.get("release")
    source_sha = source.get("commit")
    if not isinstance(release, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", release):
        raise PipelineError("Marketplace source record hint has invalid release")
    if not isinstance(source_sha, str) or not SHA_RE.fullmatch(source_sha):
        raise PipelineError("Marketplace source record hint has invalid source commit")
    return {"release": release, "source_sha": source_sha}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build and validate one Website Candidate")
    build.add_argument("--project-root", type=Path, default=Path.cwd())
    build.add_argument("--agentsmd-root", type=Path, required=True)
    build.add_argument("--marketplace-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--receipt", type=Path)
    build.add_argument("--gitpix-root", type=Path)
    build.add_argument("--gitpix-inputs", type=Path)

    resolve = subparsers.add_parser(
        "release-inputs", help="Read the exact Project release hint from Marketplace"
    )
    resolve.add_argument("--marketplace-root", type=Path, required=True)
    resolve.add_argument(
        "--source-record", default="plugins/agentsmd/SOURCE.json"
    )
    resolve.add_argument("--github-output", type=Path)

    validate = subparsers.add_parser("validate", help="Validate an existing Website Candidate")
    validate.add_argument("--output", type=Path, required=True)
    validate.add_argument("--receipt", type=Path, required=True)

    status = subparsers.add_parser("live-status", help="Compare the candidate and live manifests")
    status.add_argument("--output", type=Path, required=True)
    status.add_argument("--base-url", default="https://toolbox.md")
    status.add_argument("--report", type=Path)
    status.add_argument("--github-output", type=Path)

    live = subparsers.add_parser("verify-live", help="Verify the exact deployed Website Candidate")
    live.add_argument("--output", type=Path, required=True)
    live.add_argument("--candidate-receipt", type=Path, required=True)
    live.add_argument("--receipt", type=Path, required=True)
    live.add_argument("--base-url", default="https://toolbox.md")
    live.add_argument("--deployment-url")
    live.add_argument("--run-url")
    live.add_argument("--attempts", type=int, default=12)
    live.add_argument("--delay", type=float, default=5.0)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            receipt = build_site(
                project_root=args.project_root,
                agentsmd_root=args.agentsmd_root,
                marketplace_root=args.marketplace_root,
                output_root=args.output,
                gitpix_root=args.gitpix_root,
                gitpix_inputs=args.gitpix_inputs,
            )
            if args.receipt:
                write_json(args.receipt, receipt)
            print(
                json.dumps(
                    {
                        "state": "valid",
                        "version": receipt["project"]["version"],
                        "sourceSha": receipt["project"]["sourceSha"],
                        "manifestSha256": receipt["website"]["manifestSha256"],
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "release-inputs":
            result = released_source_hint(args.marketplace_root, args.source_record)
            if args.github_output:
                write_github_output(args.github_output, result)
            print(json.dumps(result, sort_keys=True))
        elif args.command == "validate":
            receipt = read_json(args.receipt)
            print(json.dumps(validate_output(args.output, receipt), sort_keys=True))
        elif args.command == "live-status":
            result = live_status(args.output, args.base_url)
            if args.report:
                write_json(args.report, result)
            if args.github_output:
                write_github_output(args.github_output, {"stale": result["stale"], "state": result["state"]})
            print(json.dumps(result, sort_keys=True))
        elif args.command == "verify-live":
            receipt = verify_live(
                output=args.output,
                candidate_receipt=read_json(args.candidate_receipt),
                base_url=args.base_url,
                deployment_url=args.deployment_url,
                run_url=args.run_url,
                attempts=args.attempts,
                delay=args.delay,
            )
            write_json(args.receipt, receipt)
            print(json.dumps({"state": receipt["state"], "checks": len(receipt["liveVerification"]["checks"])}, sort_keys=True))
        else:
            raise PipelineError(f"Unsupported command: {args.command}")
    except (PipelineError, ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"state": "blocked", "stage": args.command, "reason": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
