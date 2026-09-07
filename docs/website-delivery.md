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

`scripts/site_pipeline.py` preserves the released AgentsMD chain:

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

Production deployment requires both Agent Module pilot Issues to be closed:

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


## GitPix App inputs and public representation

The `/gitpix` Website Candidate joins the existing complete-site pipeline. The
Project Registry owns only source pointers, deployment workflow mapping,
canonical route, and the reviewed major boundary (GitPix 3). The GitPix source
owns VERSION, `src/lib/api-version.ts`, the App Project Record, delivery profile,
activation policy, and generated API documentation. Only `public/openapi.json`
and `public/llms.txt` are copied verbatim. No private source tree or README is
published. Private source release links are labeled provenance. Public API links
point to `/gitpix/openapi.json` and `/gitpix/llms.txt`.

The resolver selects the newest `main` Activate Release workflow run without
filtering by success. It refreshes that run and binds its current exact attempt.
A failed or unfinished latest activation blocks publication instead of falling
back to an older success. It binds repository ID 1119470408, workflow path, event,
branch, workflow source SHA and receipt artifact provenance. It downloads the
exact named receipt ZIP with `gh api`, verifies GitHub's SHA-256, and accepts only its sole bounded
regular matching JSON file. Expired, missing, ambiguous and mismatched artifacts
fail closed. Earlier attempt artifacts cannot substitute for the current one.
The successful receipt must prove healthy activation on the expected Bigbrain
target and bind an exact successful main-push Release Candidate run and attempt.
The activation workflow revision may differ from the released payload revision:
GitHub runs `workflow_run` at the default-branch commit, while its triggering
Release Candidate identifies the activated source. The receipt ZIP is bound to
the activation workflow SHA; payload metadata, trigger, release manifest and
checkout are independently bound to the released source SHA. See
[GitHub workflow_run semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run).
The published stable GitHub Release manifest, its downloaded digest, source
checkout tag, Project version, API version and activated payload must agree.

`fetch-inputs` writes a private local metadata bundle including bounded original
receipt ZIP and manifest bytes for independent offline digest revalidation. It
never writes a token. Do not upload `.inputs` as an artifact. The public receipt
contains only selected identities, download digests and coordinator-owned
nonsecret proof. The builder does not run GitPix code or install dependencies.

```sh
python3 scripts/gitpix_pipeline.py fetch-inputs \
  --output .inputs/gitpix-release.json --github-output "$GITHUB_OUTPUT"
python3 scripts/site_pipeline.py build \
  --project-root . --agentsmd-root .inputs/agentsmd \
  --marketplace-root .inputs/marketplace --gitpix-root .inputs/gitpix \
  --gitpix-inputs .inputs/gitpix-release.json \
  --output dist/site --receipt dist/website-parity-candidate.json
python3 -m unittest discover -s tests -v
```

## GitPix credential and publication boundary

Pull requests run only the unprivileged deterministic test job. It receives no
private-source or Cloudflare secrets. The production job requires successful
tests, an event other than `pull_request`, and exactly `refs/heads/main`. It uses
the `production` environment. Before enabling it, the operator must approve and
configure a main-only deployment branch policy on that environment and a
GitPix-only `GITPIX_RELEASE_READ_TOKEN` with **Contents: read** and **Actions:
read**. Keep this credential in the production environment only. No broad
existing human token is copied, and no Issues permission is needed. This change
implements the workflow boundary; it does not create the credential or change
GitHub environment settings. Existing Cloudflare credentials and the `toolbox-md`
Pages project remain the deployment seam.

While public owning Issue `toolboxmd/toolbox.md#16` is open, first GitPix
publication also requires checked-in operational evidence whose exact version,
API version, source SHA and payload digest match this candidate and whose
`applicationLiveVerification.state` is `passed`. A blocked application still
permits a truthful Website Candidate and code review, but cannot replace the
current public site. Issue #16 closes only after the exact first-release
application Live Verification, website Live Verification, and GitPix #8 terminal
proof are verified together. It must never close merely because code is merged.
After #16 closes, it is the public historical first-App-pilot completion signal.
Later healthy released activations may publish current facts with application
verification `pending`; a prior release's application pass is never inherited.
The existing AgentsMD and Marketplace closure gates still apply in both cases.

Missing private read authorization, failed resolution, invalid candidates, open
pilot gates, and upload failures preserve the Last Known Good Publication.
Website failures do not change the independently recorded GitPix runtime health.

## App evidence and hash boundary

Coordinator-owned `.toolboxmd/evidence/gitpix-pilot.json` is immutable historical
proof keyed by `identity: {version, apiVersion, sourceSha, payloadSha256}`. Its
state objects record implementation, deterministic proof, independent review,
merge, rollback and authenticated application Live Verification separately.
Missing evidence or any identity mismatch leaves those states pending. A known
documentation defect may be recorded as identity-matching
`states.documentation.state: blocked`; this blocks publication even after the
first pilot closes. `released` documentation otherwise means exact released
source bytes, not independent semantic accuracy of every API guide claim. Runtime
release, artifact, deployment, activation and health come from verified release
and activation inputs. Documentation, website publication, website verification
and SEO remain distinct states.

`/gitpix/release.json`, the page, the App receipt and whole-site manifest share
one release identity. `/gitpix/parity.json` is explicitly a candidate/source
snapshot hashed into the complete manifest, so it makes no later website
publication claim. The separate uploaded live Website Parity Receipt records
completed website publication and byte verification. This avoids circular
manifest/receipt hashes. Existing Agent-only receipt documents remain valid; an
optional `apps` array carries the separately versioned App receipt schema.
The stdlib builder validates the closed keyword subset used by the two App JSON
Schemas and rejects unsupported schema keywords rather than ignoring them.

GitPix canonical metadata, SoftwareApplication JSON-LD, homepage discovery,
`llms.txt`, robots and sitemap are generated before publication. Full SEO remains
deferred to the Delivery System SEO extension. A future GitPix major version
requires a complete website review before raising `reviewedMajor`.
