"""GitPix-only released activation resolution and public representation.

Network access is confined to fetch_inputs. The builder revalidates the saved
nonsecret evidence and exact checkout, and copies two allowlisted public files.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import io
import json
import os
import re
import stat
import subprocess
import zipfile
from pathlib import Path
from string import Template
from typing import Any
import html

from site_pipeline import (PipelineError, read_json, write_json, sha256_bytes,
    sha256_file, safe_file, verify_release_checkout, normalize_version, SHA_RE, SHA256_RE)

REPO = "toolboxmd/gitpix"
REPO_ID = 1119470408
ACTIVATE = ".github/workflows/activate-release.yml"
CANDIDATE = ".github/workflows/release-candidate.yml"
CANONICAL = "https://toolbox.md/gitpix"
PUBLIC_APP = "https://gitpix.toolbox.md"
RECORD_SCHEMA = "https://toolbox.md/schemas/app-project-record-v1.schema.json"
RECEIPT_SCHEMA = "https://toolbox.md/schemas/app-website-parity-receipt-v1.schema.json"
PUBLIC_DOCS = ("public/openapi.json", "public/llms.txt")
MAX_BYTES = 2 * 1024 * 1024


def need(condition: Any, message: str) -> None:
    if not condition:
        raise PipelineError("GitPix: " + message)


def obj(value: Any, label: str) -> dict:
    need(isinstance(value, dict), label + " must be an object")
    return value


def exact(value: Any, keys: set[str], label: str) -> dict:
    value = obj(value, label)
    need(set(value) == keys, label + " has missing or unsupported fields")
    return value


def integer(value: Any) -> bool:
    return type(value) is int and value > 0



def validate_app_schema(value: Any, schema: dict, label: str = "App document") -> None:
    """Validate the closed keyword subset used by our two App JSON Schemas.

    Reject new unsupported keywords so schema evolution cannot silently weaken
    the dependency-free builder. This does not resolve external references.
    """
    supported = {"$schema", "$id", "title", "description", "type", "const", "enum", "required", "properties", "additionalProperties", "items", "uniqueItems", "minItems", "maxItems", "minLength", "maxLength", "pattern", "minimum"}
    need(set(schema) <= supported, label + " schema uses unsupported keywords")
    kinds = schema.get("type", [])
    if isinstance(kinds, str):
        kinds = [kinds]
    checks = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
              "integer": type(value) is int, "boolean": type(value) is bool, "null": value is None}
    need(not kinds or any(checks.get(k, False) for k in kinds), label + " schema type mismatch")
    if "const" in schema:
        need(value == schema["const"], label + " schema const mismatch")
    if "enum" in schema:
        need(value in schema["enum"], label + " schema enum mismatch")
    if isinstance(value, dict):
        need(set(schema.get("required", [])) <= set(value), label + " schema required field missing")
        props = schema.get("properties", {})
        need(schema.get("additionalProperties", True) is not False or set(value) <= set(props), label + " schema unknown field")
        for key in value.keys() & props.keys():
            validate_app_schema(value[key], props[key], label + "." + key)
    if isinstance(value, list):
        need(len(value) >= schema.get("minItems", 0) and len(value) <= schema.get("maxItems", len(value)), label + " schema array length mismatch")
        if schema.get("uniqueItems"):
            need(len({json.dumps(v, sort_keys=True) for v in value}) == len(value), label + " schema duplicate items")
        if "items" in schema:
            for item in value:
                validate_app_schema(item, schema["items"], label + "[]")
    if isinstance(value, str):
        need(len(value) >= schema.get("minLength", 0) and len(value) <= schema.get("maxLength", len(value)), label + " schema string length mismatch")
        if "pattern" in schema:
            need(re.search(schema["pattern"], value) is not None, label + " schema pattern mismatch")
    if type(value) is int and "minimum" in schema:
        need(value >= schema["minimum"], label + " schema minimum mismatch")


def publication_gate(receipt: dict, pilot_closed: bool) -> bool:
    apps = receipt.get("apps", [])
    need(len(apps) == 1 and apps[0].get("project") == "gitpix", "publication requires exactly one GitPix receipt")
    app = apps[0]
    # A closed pilot is historical acceptance, not new-release application proof.
    if app["states"]["documentation"]["state"] in {"blocked", "failed"}:
        return False
    return pilot_closed or (app["operationalEvidence"]["state"] == "matched"
                            and app["states"]["applicationLiveVerification"]["state"] == "passed")


def run_identity(run: dict, name: str, path: str, event: str) -> None:
    obj(run, "workflow run")
    need(run.get("name") == name and run.get("path") == path, "untrusted workflow name/path")
    need(run.get("event") == event and run.get("head_branch") == "main", "untrusted event/branch")
    need(run.get("status") == "completed" and run.get("conclusion") == "success", "workflow did not succeed")
    need(run.get("repository", {}).get("id") == REPO_ID and run.get("repository", {}).get("full_name") == REPO,
         "workflow repository identity mismatch")
    need(run.get("head_repository", {}).get("id") == REPO_ID, "workflow head repository mismatch")
    need(integer(run.get("id")) and integer(run.get("run_attempt")), "invalid workflow run/attempt")
    need(isinstance(run.get("head_sha"), str) and SHA_RE.fullmatch(run["head_sha"]), "invalid workflow source SHA")


def receipt_zip(raw: bytes, artifact: dict, run: dict) -> dict:
    expected = f"gitpix-activation-receipt-{run['id']}-{run['run_attempt']}"
    need(artifact.get("name") == expected and artifact.get("expired") is False, "missing, expired or wrong receipt artifact")
    need(integer(artifact.get("id")), "invalid receipt artifact ID")
    need(artifact.get("digest") == "sha256:" + sha256_bytes(raw), "receipt ZIP digest mismatch")
    provenance = obj(artifact.get("workflow_run"), "receipt artifact provenance")
    need(provenance.get("id") == run["id"] and provenance.get("repository_id") == REPO_ID
         and provenance.get("head_repository_id") == REPO_ID and provenance.get("head_branch") == "main"
         and provenance.get("head_sha") == run["head_sha"], "receipt artifact provenance mismatch")
    need(len(raw) <= MAX_BYTES, "receipt ZIP exceeds size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = archive.infolist()
            need(len(files) == 1, "receipt ZIP must contain exactly one file")
            entry = files[0]
            mode = entry.external_attr >> 16
            need(entry.filename == expected + ".json" and not entry.is_dir()
                 and (stat.S_IFMT(mode) in (0, stat.S_IFREG)) and not entry.flag_bits & 1,
                 "receipt ZIP has an unsafe entry")
            need(entry.file_size <= MAX_BYTES, "receipt JSON exceeds size limit")
            return obj(json.loads(archive.read(entry)), "activation receipt")
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, RuntimeError) as error:
        raise PipelineError("GitPix: invalid receipt ZIP or JSON") from error


def verify_bundle(bundle: dict) -> dict:
    exact(bundle, {"schema", "activationRun", "receiptArtifact", "receipt", "triggerRun", "release", "manifest", "manifestSha256", "receiptArchive", "manifestRaw", "payloadArtifact"}, "input bundle")
    need(bundle["schema"] == 1, "unsupported input bundle schema")
    run = bundle["activationRun"]
    run_identity(run, "Activate Release", ACTIVATE, "workflow_run")
    try:
        archive = base64.b64decode(bundle["receiptArchive"], validate=True)
        manifest_raw = base64.b64decode(bundle["manifestRaw"], validate=True)
    except (binascii.Error, ValueError, TypeError) as error:
        raise PipelineError("GitPix: invalid preserved evidence bytes") from error
    need(receipt_zip(archive, bundle["receiptArtifact"], run) == bundle["receipt"], "receipt differs from verified archive")
    need(len(manifest_raw) <= MAX_BYTES and sha256_bytes(manifest_raw) == bundle["manifestSha256"], "manifest bytes digest mismatch")
    try:
        need(json.loads(manifest_raw) == bundle["manifest"], "manifest differs from verified bytes")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError("GitPix: invalid preserved manifest bytes") from error
    receipt = obj(bundle["receipt"], "activation receipt")
    need(receipt.get("schema") == 1 and receipt.get("project") == "gitpix"
         and receipt.get("operation") == "activation" and receipt.get("status") in {"activated", "idempotent"},
         "receipt does not prove successful activation")
    version = receipt.get("version")
    need(isinstance(version, str), "missing Project version")
    normalize_version(version, "GitPix Project version")
    sha = receipt.get("sourceSha")
    need(isinstance(sha, str) and SHA_RE.fullmatch(sha), "invalid receipt source SHA")
    # workflow_run executes at the default-branch workflow revision. Its receipt
    # ZIP belongs to that run SHA; the activated source belongs to the triggering
    # Release Candidate and is independently bound below.
    target = obj(receipt.get("target"), "activation target")
    need(target == {"host": "bigbrain", "service": "gitpix.service", "currentLink": "/var/www/gitpix/current"}, "activation target mismatch")
    need(receipt.get("production", {}).get("status") == "healthy"
         and receipt.get("production", {}).get("httpStatus") == 200, "runtime health was not proven")
    need(receipt.get("publicEdge", {}).get("status") == "expected"
         and receipt.get("publicEdge", {}).get("httpStatus") == 302, "public Access edge mismatch")
    artifact = obj(receipt.get("artifact"), "activated artifact")
    payload = artifact.get("payloadSha256")
    need(isinstance(payload, str) and SHA256_RE.fullmatch(payload), "invalid payload digest")
    need(integer(artifact.get("id")) and artifact.get("name") == f"gitpix-{version}-{sha}", "activated artifact identity mismatch")
    need(isinstance(artifact.get("uploadDigest"), str) and re.fullmatch(r"sha256:[a-f0-9]{64}", artifact["uploadDigest"]), "invalid activated upload digest")
    need(receipt.get("release", {}).get("payloadSha256") == payload, "activated release digest mismatch")
    trigger = obj(receipt.get("trigger"), "activation trigger")
    expected_trigger = {"workflow": "Release Candidate", "path": CANDIDATE, "event": "push", "branch": "main", "conclusion": "success", "sourceSha": sha, "repository": REPO}
    need(all(trigger.get(k) == v for k, v in expected_trigger.items()), "activation trigger mismatch")
    source_run = bundle["triggerRun"]
    run_identity(source_run, "Release Candidate", CANDIDATE, "push")
    need(source_run["id"] == trigger.get("runId") and source_run["run_attempt"] == trigger.get("runAttempt")
         and source_run["head_sha"] == sha, "Release Candidate run/attempt/source mismatch")
    payload_artifact = obj(bundle["payloadArtifact"], "payload artifact provenance")
    need(payload_artifact.get("id") == artifact["id"] and payload_artifact.get("name") == artifact["name"]
         and payload_artifact.get("digest") == artifact["uploadDigest"] and payload_artifact.get("expired") is False,
         "payload artifact upload identity mismatch")
    payload_run = obj(payload_artifact.get("workflow_run"), "payload artifact workflow provenance")
    need(payload_run.get("id") == source_run["id"] and payload_run.get("head_sha") == sha
         and payload_run.get("repository_id") == REPO_ID and payload_run.get("head_repository_id") == REPO_ID
         and payload_run.get("head_branch") == "main", "payload artifact workflow provenance mismatch")
    release = obj(bundle["release"], "published release")
    need(release.get("tag_name") == "v" + version and release.get("draft") is False
         and release.get("prerelease") is False and bool(release.get("published_at")), "release is not a published stable exact version")
    release_payloads = [a for a in release.get("assets", []) if a.get("name") == f"gitpix-{version}.tar.gz"]
    need(len(release_payloads) == 1 and release_payloads[0].get("digest") == "sha256:" + payload,
         "published release payload digest mismatch")
    manifest_assets = [a for a in release.get("assets", []) if a.get("name") == f"gitpix-{version}.manifest.json"]
    need(len(manifest_assets) == 1 and manifest_assets[0].get("digest") == "sha256:" + bundle["manifestSha256"],
         "published release manifest digest mismatch")
    manifest = obj(bundle["manifest"], "release manifest")
    need(manifest.get("schema") == 1 and manifest.get("project") == "gitpix"
         and manifest.get("version") == version and manifest.get("source", {}).get("sha") == sha,
         "release manifest source/version mismatch")
    need(manifest.get("artifact", {}).get("sha256") == payload
         and manifest.get("artifact", {}).get("digest") == "sha256"
         and manifest.get("artifact", {}).get("name") == f"gitpix-{version}.tar.gz", "release manifest payload mismatch")
    provenance = manifest.get("provenance", {})
    need(str(provenance.get("runId")) == str(source_run["id"])
         and str(provenance.get("runAttempt")) == str(source_run["run_attempt"])
         and provenance.get("workflow") == "Release Candidate"
         and provenance.get("workflowRef") == f"{REPO}/{CANDIDATE}@refs/heads/main", "manifest build provenance mismatch")
    receipt_artifact = obj(bundle["receiptArtifact"], "receipt download evidence")
    expected_name = f"gitpix-activation-receipt-{run['id']}-{run['run_attempt']}"
    need(receipt_artifact.get("name") == expected_name and receipt_artifact.get("expired") is False
         and integer(receipt_artifact.get("id")) and re.fullmatch(r"sha256:[a-f0-9]{64}", str(receipt_artifact.get("digest"))), "invalid receipt download evidence")
    need(SHA256_RE.fullmatch(str(bundle["manifestSha256"])), "invalid manifest digest")
    return {"version": version, "sourceSha": sha, "payloadSha256": payload}


def gh(endpoint: str, raw: bool = False) -> Any:
    # gh handles authenticated API redirects, keeping the token out of URLs,
    # output and custom urllib redirect handlers. Only fixed repository paths.
    need(endpoint.startswith(f"repos/{REPO}/"), "unsupported API repository")
    result = subprocess.run(["gh", "api", endpoint], capture_output=True)
    need(result.returncode == 0, "GitHub read failed for " + endpoint + "; check GitPix Contents and Actions read authorization")
    need(len(result.stdout) <= 20 * MAX_BYTES, "GitHub response exceeds size limit")
    if raw:
        return result.stdout
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError("GitPix: invalid GitHub JSON response") from error


def fetch_inputs() -> dict:
    need(bool(os.environ.get("GH_TOKEN")), "GITPIX_RELEASE_READ_TOKEN is required in the trusted production environment")
    response = gh(f"repos/{REPO}/actions/workflows/activate-release.yml/runs?branch=main&event=workflow_run&per_page=100")
    runs = obj(response, "workflow listing").get("workflow_runs", [])
    need(isinstance(runs, list) and len(runs) > 0, "no trusted activation run found")
    need(all(isinstance(r, dict) and integer(r.get("id")) for r in runs), "invalid activation run listing")
    need(len({r["id"] for r in runs}) == len(runs), "ambiguous activation run listing")
    # GitHub lists newest first. Do not hide failures or unfinished activations
    # behind an older success. Refresh the selected run to bind its current
    # attempt rather than using an attempt that went stale after listing.
    current = gh(f"repos/{REPO}/actions/runs/{runs[0]['id']}")
    run_identity(current, "Activate Release", ACTIVATE, "workflow_run")
    need(current["id"] == runs[0]["id"], "latest activation run identity mismatch")
    run = gh(f"repos/{REPO}/actions/runs/{current['id']}/attempts/{current['run_attempt']}")
    run_identity(run, "Activate Release", ACTIVATE, "workflow_run")
    need(all(run[k] == current[k] for k in ("id", "run_attempt", "head_sha")), "current activation attempt mismatch")
    artifacts = gh(f"repos/{REPO}/actions/runs/{run['id']}/artifacts?per_page=100")
    need(artifacts.get("total_count", 101) <= 100, "ambiguous paginated activation artifacts")
    name = f"gitpix-activation-receipt-{run['id']}-{run['run_attempt']}"
    matching = [a for a in artifacts.get("artifacts", []) if a.get("name") == name]
    need(len(matching) == 1, "missing or ambiguous activation receipt artifact")
    artifact = matching[0]
    need(integer(artifact.get("id")), "invalid artifact ID")
    raw = gh(f"repos/{REPO}/actions/artifacts/{artifact['id']}/zip", raw=True)
    receipt = receipt_zip(raw, artifact, run)
    trigger = obj(receipt.get("trigger"), "receipt trigger")
    need(integer(trigger.get("runId")) and integer(trigger.get("runAttempt")), "invalid trigger run/attempt")
    source_run = gh(f"repos/{REPO}/actions/runs/{trigger['runId']}/attempts/{trigger['runAttempt']}")
    need(integer(receipt.get("artifact", {}).get("id")), "invalid payload artifact ID")
    payload_artifact = gh(f"repos/{REPO}/actions/artifacts/{receipt['artifact']['id']}")
    version = receipt.get("version")
    need(isinstance(version, str), "missing version")
    normalize_version(version, "GitPix version")
    release = gh(f"repos/{REPO}/releases/tags/v{version}")
    manifest_name = f"gitpix-{version}.manifest.json"
    matches = [a for a in release.get("assets", []) if a.get("name") == manifest_name]
    need(len(matches) == 1 and integer(matches[0].get("id")), "missing or ambiguous published manifest")
    # Asset octet-stream response uses gh's own redirect implementation.
    result = subprocess.run(["gh", "api", f"repos/{REPO}/releases/assets/{matches[0]['id']}", "-H", "Accept: application/octet-stream"], capture_output=True)
    need(result.returncode == 0 and len(result.stdout) <= MAX_BYTES, "could not download bounded release manifest")
    raw_manifest = result.stdout
    need(matches[0].get("digest") == "sha256:" + sha256_bytes(raw_manifest), "published manifest download digest mismatch")
    try:
        manifest = json.loads(raw_manifest)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError("GitPix: invalid release manifest JSON") from error
    # Retain only the exact fields needed for offline revalidation, no API URLs,
    # actor details, installation metadata, credentials or private source bytes.
    def clean_run(r):
        return {k: r[k] for k in ("id", "run_attempt", "name", "path", "event", "head_branch", "status", "conclusion", "head_sha", "repository", "head_repository")} | {
            "repository": {"id": r["repository"]["id"], "full_name": r["repository"]["full_name"]},
            "head_repository": {"id": r["head_repository"]["id"]}}
    bundle = {"schema": 1, "activationRun": clean_run(run), "triggerRun": clean_run(source_run),
              "receiptArtifact": {k: artifact[k] for k in ("id", "name", "expired", "digest", "workflow_run")},
              "receipt": receipt, "release": {k: release[k] for k in ("tag_name", "draft", "prerelease", "published_at", "assets")},
              "manifest": manifest, "manifestSha256": sha256_bytes(raw_manifest),
              "receiptArchive": base64.b64encode(raw).decode("ascii"), "manifestRaw": base64.b64encode(raw_manifest).decode("ascii"),
              "payloadArtifact": {k: payload_artifact[k] for k in ("id", "name", "expired", "digest", "workflow_run")}}
    bundle["release"]["assets"] = [{k: a[k] for k in ("id", "name", "digest")} for a in release["assets"]]
    verify_bundle(bundle)
    return bundle


def resolve(project: dict, root: Path, bundle: dict) -> dict:
    need(project["id"] == "gitpix" and project["kind"] == "app" and project["repository"] == REPO, "unsupported App mapping")
    need(project["distribution"] == {"type": "project-deployment", "repository": REPO, "recordPath": ACTIVATE}, "deployment mapping mismatch")
    need(project["projectRecordPath"] == ".toolboxmd/project.json" and project["deliveryProfilePath"] == ".toolboxmd/delivery.json", "source mapping mismatch")
    need(project["website"] == {"repository": "toolboxmd/toolbox.md", "baseUrl": "https://toolbox.md", "route": "/gitpix"}, "website mapping mismatch")
    identity = verify_bundle(bundle)
    verify_release_checkout(root, "v" + identity["version"], identity["sourceSha"])
    record_path = safe_file(root, project["projectRecordPath"], "App Project Record")
    record = exact(read_json(record_path), {"$schema", "id", "kind", "outcome", "factSources"}, "App Project Record")
    need(record["$schema"] == RECORD_SCHEMA and record["id"] == "gitpix" and record["kind"] == "app", "Project Record schema/identity mismatch")
    need(isinstance(record["outcome"], str) and 0 < len(record["outcome"]) <= 1024, "invalid Project outcome")
    facts = exact(record["factSources"], {"version", "delivery", "documentation", "requirements", "proof"}, "App factSources")
    exact(facts["delivery"], {"release", "runtime", "activation"}, "App delivery pointers")
    for key in ("documentation", "requirements", "proof"):
        need(isinstance(facts[key], list) and len(facts[key]) > 0 and all(isinstance(p, str) for p in facts[key]) and len(set(facts[key])) == len(facts[key]), "invalid App pointer list")
    for pointer in [facts["version"], *facts["delivery"].values(), *facts["documentation"], *facts["requirements"], *facts["proof"]]:
        safe_file(root, pointer, "App source pointer")
    need(facts["version"] == "VERSION" and facts["delivery"]["activation"] == "ops/activation.json"
         and all(p in facts["documentation"] for p in PUBLIC_DOCS), "required App source pointer mismatch")
    version = safe_file(root, "VERSION", "Project version").read_text().strip()
    api_source = safe_file(root, "src/lib/api-version.ts", "API version source").read_text()
    versions = re.findall(r'^export const API_VERSION = "([^"]+)";$', api_source, re.MULTILINE)
    need(len(versions) == 1, "API version source must declare exactly one API_VERSION")
    api_version = versions[0]
    normalize_version(api_version, "GitPix API version")
    need(version == identity["version"], "source Project version mismatch")
    need(normalize_version(version, "GitPix version")[0] <= project["review"]["reviewedMajor"], "major version requires complete website review")
    profile = read_json(safe_file(root, project["deliveryProfilePath"], "delivery profile"))
    need(profile.get("website") == project["website"], "canonical website profile mismatch")
    activation = read_json(safe_file(root, facts["delivery"]["activation"], "activation policy"))
    need(activation.get("repositoryId") == REPO_ID and activation.get("publicUrl") == PUBLIC_APP
         and activation.get("host") == "bigbrain" and activation.get("service") == "gitpix.service", "source activation policy mismatch")
    docs = {p: safe_file(root, p, "public API documentation").read_bytes() for p in PUBLIC_DOCS}
    spec = json.loads(docs["public/openapi.json"])
    info = obj(spec.get("info"), "OpenAPI info")
    need(info.get("version") == api_version and info.get("x-project-version") == version
         and info.get("x-project-website") == CANONICAL, "stale OpenAPI Project/API identity")
    prose = docs["public/llms.txt"].decode("utf-8")
    need(f"Project version: {version} (VERSION). API version: {api_version} (API_VERSION)." in prose
         and f"Canonical Project website: {CANONICAL}" in prose, "stale prose API identity")
    identity["apiVersion"] = api_version
    return {"identity": identity, "record": record, "recordSha256": sha256_file(record_path), "docs": docs, "bundle": bundle}


def app_receipt(resolved: dict, evidence: dict | None, evidence_sha256: str | None = None) -> dict:
    identity = resolved["identity"]
    states = {key: {"state": "pending"} for key in ("implementation", "proof", "review", "merge", "rollback", "applicationLiveVerification")}
    evidence_match = False
    if evidence is not None:
        need(evidence.get("schema") == 1 and evidence.get("project") == "gitpix", "invalid operational evidence identity")
        evidence_match = evidence.get("identity") == identity
    states.update({"release": {"state": "published"}, "artifact": {"state": "verified"},
                   "deployment": {"state": "deployed"}, "activation": {"state": resolved["bundle"]["receipt"]["status"], "observedAt": resolved["bundle"]["receipt"].get("completedAt")},
                   "health": {"state": "healthy", "observedAt": resolved["bundle"]["receipt"].get("completedAt")}, "documentation": {"state": "released"},
                   "websitePublication": {"state": "candidate"}, "websiteVerification": {"state": "pending"},
                   "seo": {"state": "generated", "fullProgram": "deferred", "laterWorkflow": "Delivery System SEO extension"}})
    if evidence_match:
        for key, value in obj(evidence.get("states"), "operational states").items():
            need(key in {"implementation", "proof", "review", "merge", "rollback", "applicationLiveVerification", "documentation"}
                 and isinstance(value, dict) and value.get("state") in {"passed", "pending", "unknown", "failed", "blocked"}, "unsupported operational state")
            states[key] = value
    return {"$schema": RECEIPT_SCHEMA, "schema": 1, "project": "gitpix", "identity": identity,
            "states": states, "operationalEvidence": {"state": "matched" if evidence_match else "pending", "sha256": evidence_sha256 if evidence_match else None,
                "sourcePath": ".toolboxmd/evidence/gitpix-pilot.json" if evidence_match else None,
                "references": evidence.get("evidence", []) if evidence_match else []},
            "documentation": {"/gitpix/" + Path(p).name: sha256_bytes(content) for p, content in resolved["docs"].items()},
            "provenance": {"repository": REPO, "activationRunId": resolved["bundle"]["activationRun"]["id"],
                           "activationRunAttempt": resolved["bundle"]["activationRun"]["run_attempt"],
                           "receiptArtifact": {k: resolved["bundle"]["receiptArtifact"][k] for k in ("id", "name", "digest")}, "manifestSha256": resolved["bundle"]["manifestSha256"]}}


def render(output: Path, project_root: Path, resolved: dict, evidence: dict | None) -> dict:
    validate_app_schema(resolved["record"], read_json(project_root / "schemas/app-project-record-v1.schema.json"), "App Project Record")
    evidence_path = project_root / ".toolboxmd/evidence/gitpix-pilot.json"
    receipt = app_receipt(resolved, evidence, sha256_file(evidence_path) if evidence is not None else None)
    validate_app_schema(receipt, read_json(project_root / "schemas/app-website-parity-receipt-v1.schema.json"), "App parity receipt")
    identity = resolved["identity"]
    page_root = output / "gitpix"
    page_root.mkdir()
    for source, content in resolved["docs"].items():
        (page_root / Path(source).name).write_bytes(content)
    write_json(page_root / "release.json", {"schema": 1, "project": "gitpix", **identity, "canonicalUrl": CANONICAL, "applicationUrl": PUBLIC_APP})
    write_json(page_root / "parity.json", receipt)
    data = json.dumps({"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "GitPix", "applicationCategory": "MultimediaApplication", "softwareVersion": identity["version"], "url": CANONICAL, "description": resolved["record"]["outcome"]}).replace("<", "\\u003c")
    page = Template((project_root / "templates/gitpix.html").read_text()).substitute(
        outcome=html.escape(resolved["record"]["outcome"], quote=True), structured_data=data,
        **{key: html.escape(value, quote=True) for key, value in identity.items()},
        app_verification=html.escape(receipt["states"]["applicationLiveVerification"]["state"]))
    (page_root / "index.html").write_text(page)
    for name in ("app-project-record-v1.schema.json", "app-website-parity-receipt-v1.schema.json"):
        (output / "schemas" / name).write_bytes(safe_file(project_root, "schemas/" + name, "App public schema").read_bytes())
    with (output / "llms.txt").open("a") as stream:
        stream.write(f"\n## GitPix v{identity['version']}\n\n{resolved['record']['outcome']}\n\n- Canonical page: {CANONICAL}\n- OpenAPI: {CANONICAL}/openapi.json\n- API prose: {CANONICAL}/llms.txt\n- Exact release identity: {CANONICAL}/release.json\n- API version: {identity['apiVersion']}\n- Source commit: {identity['sourceSha']}\n- Deployed payload SHA-256: {identity['payloadSha256']}\n- Open GitPix: {PUBLIC_APP} (Cloudflare Access authenticated session required)\n")
    sitemap = output / "sitemap.xml"
    sitemap.write_text(sitemap.read_text().replace("</urlset>", f"  <url><loc>{CANONICAL}</loc></url>\n</urlset>"))
    with (output / "_headers").open("a") as stream:
        stream.write("\n/gitpix/*.json\n  Content-Type: application/json\n  X-Content-Type-Options: nosniff\n\n/gitpix/llms.txt\n  Content-Type: text/plain; charset=utf-8\n  X-Content-Type-Options: nosniff\n")
    return receipt


def validate_public(output: Path, receipt: dict) -> None:
    validate_app_schema(receipt, read_json(output / "schemas/app-website-parity-receipt-v1.schema.json"), "App parity receipt")
    identity = read_json(safe_file(output, "gitpix/release.json", "public release identity"))
    need(all(identity.get(k) == v for k, v in receipt["identity"].items()), "public release identity mismatch")
    need(read_json(safe_file(output, "gitpix/parity.json", "public parity receipt")) == receipt, "public parity receipt mismatch")
    for route, digest in receipt["documentation"].items():
        need(sha256_file(safe_file(output, route.lstrip("/"), "public documentation")) == digest, "public documentation digest mismatch")
    page = safe_file(output, "gitpix/index.html", "GitPix page").read_text()
    need(all(value in page for value in receipt["identity"].values()), "page identity mismatch")
    need('rel="canonical" href="' + CANONICAL + '"' in page and "Cloudflare Access" in page, "page canonical/access missing")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch-inputs", "publication-gate"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--pilot-state", choices=["open", "closed"])
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "publication-gate":
            need(args.receipt is not None and args.pilot_state is not None, "publication gate requires receipt and pilot state")
            ready = publication_gate(read_json(args.receipt), args.pilot_state == "closed")
            print("true" if ready else "false")
            return 0
        need(args.output is not None, "fetch-inputs requires output")
        bundle = fetch_inputs()
        write_json(args.output, bundle)
        if args.github_output:
            with args.github_output.open("a") as stream:
                stream.write("source_sha=" + bundle["receipt"]["sourceSha"] + "\n")
        print(json.dumps({"state": "resolved", **verify_bundle(bundle)}))
        return 0
    except (PipelineError, KeyError, TypeError, ValueError) as error:
        print(json.dumps({"state": "blocked", "project": "gitpix", "stage": "release-inputs", "reason": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
