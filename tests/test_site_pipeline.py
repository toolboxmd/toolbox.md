import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from site_pipeline import (  # noqa: E402
    PipelineError,
    build_site,
    extract_codex_install_commands,
    parse_skill_frontmatter,
    released_source_hint,
    live_status,
    safe_file,
    validate_registry,
    verify_live,
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class SitePipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.toolbox = self.base / "toolbox"
        self.agentsmd = self.base / "agentsmd"
        self.marketplace = self.base / "marketplace"
        self.output = self.base / "output"
        self._make_toolbox()
        self._make_agentsmd()
        self._make_marketplace()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _init_repo(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        git(root, "init", "-q")
        git(root, "config", "user.name", "Test User")
        git(root, "config", "user.email", "test@example.com")

    def _commit_and_tag(self, root: Path, tag: str) -> str:
        git(root, "add", ".")
        git(root, "commit", "-qm", f"Release {tag}")
        git(root, "tag", "-a", tag, "-m", f"Release {tag}")
        return git(root, "rev-parse", "HEAD")

    def _make_toolbox(self) -> None:
        self._init_repo(self.toolbox)
        (self.toolbox / "index.html").write_text(
            '<!doctype html><html><body><a href="@@AGENTSMD_ROUTE@@">AgentsMD '
            'v@@AGENTSMD_VERSION@@</a><p>@@AGENTSMD_OUTCOME@@</p></body></html>\n',
            encoding="utf-8",
        )
        for name in ("ascii.json", "hand-left.json", "hand-right.json"):
            (self.toolbox / name).write_text("{}\n", encoding="utf-8")
        for relative in (
            "schemas/project-registry-v1.schema.json",
            "schemas/website-parity-receipt-v1.schema.json",
            "templates/agentsmd.html",
        ):
            destination = self.toolbox / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / relative).read_bytes())
        registry = {
            "$schema": "https://toolbox.md/schemas/project-registry-v1.schema.json",
            "schema": 1,
            "projects": [
                {
                    "id": "agentsmd",
                    "repository": "toolboxmd/agentsmd",
                    "kind": "agent-module",
                    "projectRecordPath": ".toolboxmd/project.json",
                    "deliveryProfilePath": ".toolboxmd/delivery.json",
                    "distribution": {
                        "type": "marketplace",
                        "repository": "toolboxmd/marketplace",
                        "recordPath": "plugins/agentsmd/SOURCE.json",
                        "catalogPath": "catalog.json",
                    },
                    "website": {
                        "repository": "toolboxmd/toolbox.md",
                        "baseUrl": "https://toolbox.md",
                        "route": "/agentsmd",
                    },
                    "review": {"reviewedMajor": 8},
                }
            ],
        }
        write_json(self.toolbox / ".toolboxmd/projects.json", registry)
        self.toolbox_sha = self._commit_and_tag(self.toolbox, "v0.2.0")

    def _make_agentsmd(self) -> None:
        self._init_repo(self.agentsmd)
        files = {
            "VERSION": "8.6.1\n",
            "README.md": (
                "# AgentsMD\n\nReleased workflow suite.\n\n"
                "### Codex\n\n```sh\n"
                "codex plugin marketplace add toolboxmd/marketplace\n"
                "codex plugin add agentsmd@toolboxmd\n"
                "```\n"
            ),
            "SKILL_CATALOGUE.md": "# Skill Catalogue\n",
            "docs/adr/0001.md": "# Persistent automation\n",
            "skills/alpha/SKILL.md": (
                "---\nname: alpha\ndescription: Alpha does one useful thing.\n---\n\n# Alpha\n"
            ),
            "skills/beta/SKILL.md": (
                "---\nname: beta\ndescription: >-\n  Beta handles a second useful thing\n  with supporting material.\n---\n\n# Beta\n"
            ),
            "skills/beta/references/guide.md": "# Guide\n",
            ".codex-plugin/plugin.json": json.dumps(
                {
                    "name": "agentsmd",
                    "displayName": "AgentsMD",
                    "version": "8.6.1",
                    "description": "Released workflow suite.",
                    "author": {"name": "ToolboxMD"},
                    "homepage": "https://github.com/toolboxmd/agentsmd",
                    "repository": "https://github.com/toolboxmd/agentsmd",
                    "license": "MIT",
                }
            )
            + "\n",
        }
        for relative, content in files.items():
            path = self.agentsmd / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        project = {
            "$schema": "https://raw.githubusercontent.com/toolboxmd/marketplace/v0.3.0/schemas/project-record-v1.schema.json",
            "id": "agentsmd",
            "kind": "agent-module",
            "outcome": "Align agent work with mission and Project Direction.",
            "factSources": {
                "version": "VERSION",
                "delivery": {"codex": ".codex-plugin/plugin.json"},
                "skills": ["skills/alpha/SKILL.md", "skills/beta/SKILL.md"],
                "documentation": ["README.md", "SKILL_CATALOGUE.md", "docs/adr/0001.md"],
            },
        }
        write_json(self.agentsmd / ".toolboxmd/project.json", project)
        delivery = {
            "$schema": "https://raw.githubusercontent.com/toolboxmd/agentsmd/v8.6.0/schemas/delivery-v1.schema.json",
            "schema": 1,
            "commands": {"changedScope": ["true"], "complete": ["true"], "release": ["true"]},
            "website": {
                "repository": "toolboxmd/toolbox.md",
                "baseUrl": "https://toolbox.md",
                "route": "/agentsmd",
            },
        }
        write_json(self.agentsmd / ".toolboxmd/delivery.json", delivery)
        self.agentsmd_sha = self._commit_and_tag(self.agentsmd, "v8.6.1")

    def _make_marketplace(self) -> None:
        self._init_repo(self.marketplace)
        (self.marketplace / "VERSION").write_text("1.2.6\n", encoding="utf-8")
        source_files = []
        included = [
            ".toolboxmd/project.json",
            "VERSION",
            ".codex-plugin/plugin.json",
            "README.md",
            "SKILL_CATALOGUE.md",
            "docs/adr/0001.md",
            "skills/alpha/SKILL.md",
            "skills/beta/SKILL.md",
            "skills/beta/references/guide.md",
        ]
        for relative in included:
            bundled = self.marketplace / "plugins/agentsmd" / relative
            bundled.parent.mkdir(parents=True, exist_ok=True)
            bundled.write_bytes((self.agentsmd / relative).read_bytes())
            source_files.append(
                {
                    "path": relative,
                    "source": relative,
                    "sha256": sha256(self.agentsmd / relative),
                    "mode": "100644",
                }
            )
        source = {
            "schema": 1,
            "project": "agentsmd",
            "release": "v8.6.1",
            "commit": self.agentsmd_sha,
            "projectRecord": {
                "path": ".toolboxmd/project.json",
                "sha256": sha256(self.agentsmd / ".toolboxmd/project.json"),
            },
            "manifestFieldSources": {
                "name": ".toolboxmd/project.json",
                "version": "VERSION",
                "description": ".toolboxmd/project.json",
                "skills": ".toolboxmd/project.json",
            },
            "files": source_files,
        }
        write_json(self.marketplace / "plugins/agentsmd/SOURCE.json", source)
        write_json(
            self.marketplace / "catalog.json",
            {
                "name": "toolboxmd",
                "plugins": [
                    {
                        "name": "agentsmd",
                        "description": "Align agent work with mission and Project Direction.",
                        "github": "toolboxmd/agentsmd",
                        "release": "v8.6.1",
                        "sha": self.agentsmd_sha,
                        "category": "Developer Tools",
                        "kind": "agent-module",
                        "projectRecord": {
                            "path": ".toolboxmd/project.json",
                            "sha256": source["projectRecord"]["sha256"],
                        },
                    }
                ],
            },
        )
        self.marketplace_sha = self._commit_and_tag(self.marketplace, "v1.2.6")

    def build(self, output: Path | None = None) -> dict:
        return build_site(
            project_root=self.toolbox,
            agentsmd_root=self.agentsmd,
            marketplace_root=self.marketplace,
            output_root=output or self.output,
        )

    def test_build_resolves_exact_release_and_generates_discovery(self) -> None:
        receipt = self.build()

        self.assertEqual("8.6.1", receipt["project"]["version"])
        self.assertEqual(self.agentsmd_sha, receipt["project"]["sourceSha"])
        self.assertEqual(self.marketplace_sha, receipt["distribution"]["sourceSha"])
        self.assertEqual("candidate", receipt["websiteDeployment"]["state"])

        page = (self.output / "agentsmd/index.html").read_text(encoding="utf-8")
        self.assertIn("AgentsMD v8.6.1", page)
        self.assertIn(self.agentsmd_sha, page)
        self.assertIn('rel="canonical" href="https://toolbox.md/agentsmd"', page)
        self.assertIn("codex plugin add agentsmd@toolboxmd", page)
        self.assertIn(f"blob/{self.agentsmd_sha}/README.md", page)

        homepage = (self.output / "index.html").read_text(encoding="utf-8")
        self.assertIn("AgentsMD v8.6.1", homepage)
        self.assertIn("Align agent work with mission and Project Direction.", homepage)
        self.assertNotIn("@@AGENTSMD_", homepage)

        llms = (self.output / "llms.txt").read_text(encoding="utf-8")
        self.assertIn("AgentsMD v8.6.1", llms)
        self.assertNotIn("<html", llms.lower())

        index_path = self.output / ".well-known/agent-skills/index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(
            "https://schemas.agentskills.io/discovery/0.2.0/schema.json",
            index["$schema"],
        )
        self.assertEqual(["alpha", "beta"], [skill["name"] for skill in index["skills"]])

        alpha = index["skills"][0]
        alpha_path = self.output / alpha["url"].lstrip("/")
        self.assertEqual("skill-md", alpha["type"])
        self.assertEqual(f"sha256:{sha256(alpha_path)}", alpha["digest"])

        beta = index["skills"][1]
        beta_path = self.output / beta["url"].lstrip("/")
        self.assertEqual("archive", beta["type"])
        self.assertEqual(f"sha256:{sha256(beta_path)}", beta["digest"])
        with tarfile.open(fileobj=io.BytesIO(beta_path.read_bytes()), mode="r:gz") as archive:
            self.assertEqual(
                ["SKILL.md", "references/guide.md"],
                sorted(member.name for member in archive.getmembers() if member.isfile()),
            )
            self.assertFalse(any(member.pax_headers for member in archive.getmembers()))

    def test_candidate_is_deterministic(self) -> None:
        first = self.base / "first"
        second = self.base / "second"
        self.build(first)
        self.build(second)

        first_files = {
            path.relative_to(first): sha256(path)
            for path in first.rglob("*")
            if path.is_file()
        }
        second_files = {
            path.relative_to(second): sha256(path)
            for path in second.rglob("*")
            if path.is_file()
        }
        self.assertEqual(first_files, second_files)

    def test_failed_candidate_preserves_previous_output(self) -> None:
        self.output.mkdir()
        sentinel = self.output / "last-known-good.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        source_path = self.marketplace / "plugins/agentsmd/SOURCE.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source["projectRecord"]["sha256"] = "0" * 64
        write_json(source_path, source)

        with self.assertRaisesRegex(PipelineError, "Project Record digest"):
            self.build()

        self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))
        self.assertEqual([sentinel], list(self.output.iterdir()))

    def test_failed_generated_candidate_preserves_previous_output(self) -> None:
        self.output.mkdir()
        sentinel = self.output / "last-known-good.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        template_path = self.toolbox / "templates/agentsmd.html"
        template_path.write_text(
            template_path.read_text(encoding="utf-8").replace(
                "Generated from Project and Marketplace truth.",
                "Generated from Project and Marketplace truth — invalid.",
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(PipelineError, "em dash"):
            self.build()

        self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))
        self.assertEqual([sentinel], list(self.output.iterdir()))

    def test_unreviewed_major_release_fails_closed(self) -> None:
        registry_path = self.toolbox / ".toolboxmd/projects.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["projects"][0]["review"]["reviewedMajor"] = 7
        write_json(registry_path, registry)

        with self.assertRaisesRegex(PipelineError, "complete website review"):
            self.build()

    def test_registry_rejects_release_facts(self) -> None:
        registry = json.loads(
            (self.toolbox / ".toolboxmd/projects.json").read_text(encoding="utf-8")
        )
        registry["projects"][0]["version"] = "8.6.1"

        with self.assertRaisesRegex(PipelineError, "unknown field.*version"):
            validate_registry(registry)

    def test_multiline_frontmatter_is_normalized(self) -> None:
        name, description = parse_skill_frontmatter(
            "---\nname: useful-skill\ndescription: >-\n  First line\n  second line.\n---\n# Body\n"
        )
        self.assertEqual("useful-skill", name)
        self.assertEqual("First line second line.", description)

    def test_codex_install_commands_are_source_grounded(self) -> None:
        commands, path = extract_codex_install_commands(
            [
                ("README.md", "# Readme\n\n### Codex\n\n```sh\ncodex plugin add example\n```\n"),
            ]
        )
        self.assertEqual("codex plugin add example", commands)
        self.assertEqual("README.md", path)

    def test_release_hint_is_strict(self) -> None:
        self.assertEqual(
            {"release": "v8.6.1", "source_sha": self.agentsmd_sha},
            released_source_hint(self.marketplace, "plugins/agentsmd/SOURCE.json"),
        )

    def test_safe_file_rejects_symlinks(self) -> None:
        target = self.base / "target.txt"
        target.write_text("secret\n", encoding="utf-8")
        link = self.toolbox / "link.txt"
        link.symlink_to(target)

        with self.assertRaisesRegex(PipelineError, "symbolic link"):
            safe_file(self.toolbox, "link.txt", "fixture")

    def test_live_status_detects_current_and_stale_manifests(self) -> None:
        self.build()
        manifest = (self.output / "site-manifest.json").read_bytes()
        with patch(
            "site_pipeline.fetch_url",
            return_value=(manifest, "application/json", "https://toolbox.md/site-manifest.json"),
        ):
            self.assertEqual("current", live_status(self.output, "https://toolbox.md")["state"])
        with patch(
            "site_pipeline.fetch_url",
            return_value=(b'{"schema":1}', "application/json", "https://toolbox.md/site-manifest.json"),
        ):
            self.assertEqual("stale", live_status(self.output, "https://toolbox.md")["state"])

    def test_live_verification_matches_exact_public_bytes(self) -> None:
        receipt = self.build()
        content_types = {
            "/": "text/html",
            "/agentsmd": "text/html",
            "/llms.txt": "text/plain",
            "/.well-known/agent-skills/index.json": "application/json",
            "/site-manifest.json": "application/json",
            "/robots.txt": "text/plain",
            "/sitemap.xml": "application/xml",
        }
        local_paths = {
            "/": "index.html",
            "/agentsmd": "agentsmd/index.html",
            "/llms.txt": "llms.txt",
            "/.well-known/agent-skills/index.json": ".well-known/agent-skills/index.json",
            "/site-manifest.json": "site-manifest.json",
            "/robots.txt": "robots.txt",
            "/sitemap.xml": "sitemap.xml",
        }

        def fake_fetch(url: str, timeout: float = 20.0) -> tuple[bytes, str, str]:
            del timeout
            from urllib.parse import urlparse

            parsed = urlparse(url)
            route = parsed.path
            if parsed.hostname == "toolbox.md":
                if route in local_paths:
                    return (
                        (self.output / local_paths[route]).read_bytes(),
                        content_types[route],
                        url,
                    )
                artifact = self.output / route.lstrip("/")
                if artifact.is_file():
                    return artifact.read_bytes(), "application/octet-stream", url
            return b"public link", "text/html", url

        with patch("site_pipeline.fetch_url", side_effect=fake_fetch):
            live = verify_live(
                output=self.output,
                candidate_receipt=receipt,
                base_url="https://toolbox.md",
                deployment_url="https://example.pages.dev",
                run_url="https://github.com/toolboxmd/toolbox.md/actions/runs/1",
                attempts=1,
                delay=0,
            )
        self.assertEqual("live-verified", live["state"])
        self.assertEqual("passed", live["liveVerification"]["state"])
        self.assertGreater(len(live["liveVerification"]["checks"]), 10)

    def test_workflow_validates_before_deploy_and_keeps_pilot_gate(self) -> None:
        workflow = (ROOT / ".github/workflows/website-parity.yml").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            workflow.index("Build and validate the isolated Website Candidate"),
            workflow.index("Deploy the validated candidate to Cloudflare Pages"),
        )
        self.assertIn("repos/toolboxmd/agentsmd/issues/57", workflow)
        self.assertIn("repos/toolboxmd/marketplace/issues/17", workflow)
        self.assertIn('cron: "17 * * * *"', workflow)
        self.assertIn("pages deploy dist/site", workflow)
        self.assertNotRegex(workflow, r"uses: [^\n]+@v[0-9]")


if __name__ == "__main__":
    unittest.main()
