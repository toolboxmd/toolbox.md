# ADR 0001: Federated ToolboxMD project distribution

Status: Accepted
Date: 2026-08-30

## Context

ToolboxMD Projects currently repeat identity, descriptions, versions,
installation instructions, and discovery metadata across Project repositories,
host manifests, Marketplace indexes, README files, and web surfaces.

The existing lanes are disconnected:

- Project repositories own releases and detailed documentation.
- `toolboxmd/marketplace` generates host marketplace indexes.
- `toolbox.md` is the public landing page.
- Apps such as GitPix own their runtime and deployment.

We considered making each Project generate every downstream surface, making
Marketplace own every Project fact and deployment, and creating a separate
distribution system immediately.

## Decision

Use a federated, one-way publication model.

1. Each Project repository owns its Project Record, released artifact, detailed
   documentation, verification, and runtime deployment.
2. ToolboxMD Marketplace ingests approved immutable Project Records for Agent
   Modules. It owns workflow membership, bundle composition, host adapters,
   Agent Search Optimization, bootstrap installation, generated marketplace
   indexes, and distribution proof.
3. `toolbox.md` consumes released Project Records and Marketplace discovery data.
   It owns public Project pages, cross-Project navigation, machine-readable
   discovery, and the ToolboxMD Directory presentation.
4. A successful Project release proposes an exact Marketplace update. A
   scheduled reconciliation detects missed releases and drift.
5. Invalid or unproved updates preserve the last known-good publication and
   expose the blocker.
6. Provider submission, approval, publication, installation, loading, and Live
   Verification remain separate states.
7. Each Project keeps ownership of its deployment credentials and runtime.
   Cross-Project automation may coordinate deployments but does not absorb them.
8. Keep the distribution control plane inside Marketplace initially. Extract a
   separate `toolboxmd/distribution` service only when persistent state, secrets,
   independent availability, multiple non-Marketplace consumers, or separate
   ownership make that boundary necessary.

## Consequences

- Each fact has one canonical owner.
- Provider indexes and public pages become generated views rather than competing
  truth.
- Marketplace becomes deeper but remains bounded to Agent Module discovery and
  distribution.
- `toolbox.md` can list Apps and Agent Modules without making Marketplace own
  App deployment.
- Release ingestion and provider publication require explicit contracts and
  evidence.
- Some provider directories retain review gates and cannot be updated
  atomically with a GitHub release.
- A future extraction remains possible without changing the Project Record
  contract.

## Rejected alternatives

### Every Project generates every surface

Rejected because host, website, and cross-workflow behavior would drift across
Projects.

### Marketplace owns all Project documentation and deployment

Rejected because it would duplicate detailed Project truth and centralize
unrelated runtime authority.

### Create `toolboxmd/distribution` immediately

Rejected because the current scale does not justify an additional repository,
release identity, deployment, and synchronization boundary.
