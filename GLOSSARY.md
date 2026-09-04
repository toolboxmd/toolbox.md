# ToolboxMD Directory Glossary

**Project Registry**:
A toolbox.md-owned mapping from a ToolboxMD Project to its owning repository,
kind, delivery profile, distribution or deployment source, and canonical
website route. It points to authoritative facts and never owns Project release
versions, source commits, or documentation content.
_Avoid_: Product database, release registry

**Website Candidate**:
A complete, isolated public-site build generated from exact released inputs and
not yet promoted to Cloudflare Pages. Validation must pass before deployment.
_Avoid_: Draft site, staging state

**Website Parity**:
Agreement between a released Project, its distribution record, and its
canonical toolbox.md representation at the exact version and source identity.
_Avoid_: Eventual consistency, website freshness

**Website Parity Receipt**:
Structured evidence for one Website Candidate or live deployment. It records
the resolved Project identity, distribution artifact, public routes,
documentation state, deployment state, Live Verification, and SEO impact.
_Avoid_: Build log, release notes

**Last Known Good Publication**:
The newest Cloudflare Pages deployment that completed validation and Live
Verification. A failed candidate or deployment does not replace it.
_Avoid_: Latest build, current branch
