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

Build the complete Website Candidate from exact released local inputs. Follow
[GitPix input preparation](docs/website-delivery.md#gitpix-app-inputs-and-public-representation)
for approved read access and the activation metadata bundle. Check out GitPix at
the resolver's exact `source_sha`, with its release tag available. The Marketplace
release and the AgentsMD release it names must also be checked out exactly.
The builder rejects source identity mismatches and missing GitPix inputs.

```sh
python3 scripts/site_pipeline.py build \
  --project-root . \
  --agentsmd-root .inputs/agentsmd \
  --marketplace-root .inputs/marketplace \
  --gitpix-root .inputs/gitpix \
  --gitpix-inputs .inputs/gitpix-release.json \
  --output dist/site \
  --receipt dist/website-parity-candidate.json
```

Run the deterministic proof suite:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/site_pipeline.py validate \
  --output dist/site \
  --receipt dist/website-parity-candidate.json
```

The GitHub Actions workflow resolves the latest published Marketplace release,
checks out the exact AgentsMD source commit named by it, and resolves the latest
trusted GitPix activation and its exact released source. It builds both Projects
in one isolated directory. Production deployment requires the Agent Module pilot
closures and the
[GitPix first-publication gate](docs/website-delivery.md#gitpix-credential-and-publication-boundary).
A scheduled run compares the expected and live site manifests, deploys only when
stale, and emits a Website Parity Receipt after
Live Verification.

See [website delivery](docs/website-delivery.md) for the Cloudflare Pages seam,
ownership boundaries, credentials, failure behavior, and discovery contract.
