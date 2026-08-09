# Security and data lifecycle

Skeinix separates user content from control-plane routing data and performs
account erasure as a durable, phase-based job. PostgreSQL owns the purge state
machine; a job is complete only after every configured phase succeeds.

## Data surfaces

| Surface | Classification | Typical contents | Erasure and retention |
| --- | --- | --- | --- |
| PostgreSQL | Restricted | Identity, business content, durable event cursors | Row deletion or cryptographic erasure according to deployment policy |
| Object storage | Restricted | VFS objects, knowledge sources, debug snapshots, task outputs | Delete the tenant and resource prefixes according to deployment policy |
| Runtime checkpoints | Restricted | Conversation and Agent Runtime checkpoint state | Delete organization and exact-thread state |
| Redis/Valkey | Confidential | Short-lived stream progress and event copies | Delete organization/resource keys; bounded by configured TTLs |
| Celery queues | Internal | Opaque identifiers and execution routing metadata | Revoke capabilities; queued work fails closed and expires with broker policy |
| Knowledge index | Restricted | Knowledge-source chunks and vector data | Delete through the knowledge/database purge path and any configured external adapter |
| Host temporary storage | Restricted | Sandbox overlays, runtime SDK state, materialized VFS data | Delete validated tenant directories; also bounded by sandbox TTLs |
| Backups | Restricted | Encrypted historical database and object snapshots | Cryptographic protection and explicit deployment retention; never reported as immediately erased |

The executable source of truth is
`vibecanvas_api.security.purge`: its `PHASES` sequence and handler map are tested
for completeness. Deployments must document their backup expiry and any
external knowledge-index adapter separately because the application cannot
erase an operator-owned backup ahead of that policy.

## Reporting vulnerabilities

Please report suspected vulnerabilities privately to the repository maintainers
instead of opening a public issue. Include affected versions, reproduction
steps, and the potential impact. Do not include real user data or credentials.
