# toolbox.md

The public ToolboxMD Project directory for people and agents. Production is
served at <https://toolbox.md>.

## Website parity

The website is generated from released Project and distribution facts:

- `.toolboxmd/projects.json` owns Project-to-website mappings only.
- each Project Record owns Project identity and fact locations;
- ToolboxMD Marketplace owns released Agent Module distribution evidence;
- `scripts/site_pipeline.py` resolves, generates, validates, and verifies the
  exact public representation.

Build the current AgentsMD Website Candidate from released local checkouts:

```sh
python3 scripts/site_pipeline.py build \
  --project-root . \
  --agentsmd-root ../agentsmd \
  --marketplace-root ../marketplace \
  --output dist/site \
  --receipt dist/website-parity-candidate.json
```

Run the deterministic proof suite:

```sh
python3 -m unittest tests.test_site_pipeline -v
python3 scripts/site_pipeline.py validate \
  --output dist/site \
  --receipt dist/website-parity-candidate.json
```

The GitHub Actions workflow resolves the latest published Marketplace release,
checks out the exact AgentsMD source commit named by it, and builds in an
isolated directory. Production deployment remains gated on the AgentsMD and
Marketplace pilot Issues. A scheduled run compares the expected and live site
manifests, deploys only when stale, and emits a Website Parity Receipt after
Live Verification.

See [website delivery](docs/website-delivery.md) for the Cloudflare Pages seam,
ownership boundaries, credentials, failure behavior, and discovery contract.
