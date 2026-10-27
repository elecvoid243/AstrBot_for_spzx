# Contracted workflow results and immutable artifacts

Single-task workflow assets can now declare JSON Schema output contracts. Invalid
submissions return persistent field diagnostics for correction within the existing
Agent budget. Result completion atomically saves the immutable artifact version
and completion event, with idempotent submission and explicit revision requests.

Custom results use a JSON text argument with the full frozen contract in the
parameter description. This preserves composed schemas and local references across
provider serializers and two-stage skills-like parameter requests; the service
remains responsible for authoritative validation.

Runs pin input artifact versions and record actual reads. Agents and users can
read complete reports, evidence and ready files without relying on summaries or
temporary paths. Creating a revision preserves earlier versions and does not
schedule downstream work.

The existing **Workflow Assets → Start Run → Independent Runs → View Run** entry
remains in place. The former text-only result area now includes **Artifact
version**, complete structured content, file preview/download, explicit revisions
and **Result submission history**. Chinese and English instructions and actual
WebUI screenshots have been updated.

Approval binding, bounded loops and invocation checkpoint recovery remain the
responsibility of their subsequent tickets.
