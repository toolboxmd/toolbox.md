# Website delivery

## Hosting seam

The production site is a Cloudflare Pages Direct Upload project:

- Pages project: `toolbox-md`
- custom domain: `toolbox.md`
- canonical origin: `https://toolbox.md`
- production input: the validated `dist/site` directory
- deployment command: `wrangler pages deploy dist/site --project-name=toolbox-md`

This seam was identified before adding automation. The live domain returns
Cloudflare responses, while this GitHub repository has no GitHub Pages site,
deployment record, environment, or prior deployment workflow. Existing
operating records identify `toolbox-md` as the Pages project and Direct Upload
as the established path. The live page was also stale relative to this
repository, which confirms that a repository push did not deploy it.

Cloudflare documents Direct Upload as the path for uploading prebuilt assets
from a local or external CI system. A Direct Upload project cannot later switch
to Cloudflare Git integration without creating a new Pages project. This
pipeline therefore keeps Direct Upload and moves its caller from an ad hoc
local command to a validated GitHub Actions job.

References:

- <https://developers.cloudflare.com/pages/get-started/direct-upload/>
- <https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/>
- <https://developers.cloudflare.com/workers/wrangler/commands/pages/>

## Ownership

- Each Project repository owns its Project Record, release, documentation,
  delivery profile, and source commit.
- `toolboxmd/marketplace` owns the released Agent Module distribution record
  and bundle.
- `toolboxmd/toolbox.md` owns Project-to-site mappings, generated pages,
  public discovery, website validation, and Website Parity Receipts.
- Cloudflare Pages owns atomic production deployment and retains the Last Known
  Good Publication when an upload fails.

The Project Registry contains only mappings and the reviewed major-version
boundary. It does not store a release version, source SHA, description, or
documentation copy.

## Pipeline

`scripts/site_pipeline.py` resolves one released chain:

1. the latest published Marketplace release selected by CI;
2. the Marketplace `SOURCE.json` record for AgentsMD;
3. the exact AgentsMD release tag and source commit named by that record;
4. the Project Record, delivery profile, documentation, and Skill files at
   that exact commit;
5. the generated website, `llms.txt`, Vercel skills CLI compatible discovery,
   site manifest, and Website Parity Receipt.

Generation occurs in an isolated Website Candidate directory. The candidate is
validated before it replaces any local output or reaches Wrangler. Cloudflare
Pages creates a new atomic deployment, so a generation, validation, or upload
failure leaves the existing production deployment untouched.

The scheduled run compares the expected manifest with the live manifest. It
does nothing when they agree. It deploys the validated candidate when a release
was missed or the live site is stale, then performs Live Verification.

## Gates and credentials

Production deployment requires both pilot Issues to be closed:

- `toolboxmd/agentsmd#57`
- `toolboxmd/marketplace#17`

Until both are closed, pull requests and scheduled runs build and validate but
cannot deploy. This preserves the AgentsMD-first pilot order without blocking
safe implementation.

GitHub Actions must provide these repository secrets:

- `CLOUDFLARE_API_TOKEN`, scoped to Cloudflare Pages edit access;
- `CLOUDFLARE_ACCOUNT_ID`, the account that owns `toolbox-md`.

No credential belongs in the Project Registry, build output, receipt, workflow
summary, or repository history.

## Discovery boundary

`/.well-known/agent-skills/index.json` implements the format consumed by the
Vercel `skills` CLI. The generated page and `llms.txt` describe it as a
distribution compatibility endpoint. They do not claim that this well-known
URL is part of the core Agent Skills file-format specification.

Each entry is generated from an Active Skill named by the released AgentsMD
Project Record. Its artifact is copied only from files listed and hashed in the
Marketplace source record. Each published artifact has a SHA-256 digest.

## Major-version review

The Project Registry records the latest AgentsMD major version whose complete
website representation was reviewed. A newer major release fails candidate
generation until that value is deliberately updated in a reviewed change.
Patch and minor releases can flow through the normal generated path.
