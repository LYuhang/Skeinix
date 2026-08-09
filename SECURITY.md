# Security policy

## Supported versions

Skeinix is currently alpha software. Security fixes are applied to the latest
commit on the default branch. No older release line is supported yet.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in public issues, discussions, or
pull requests.

Until a dedicated security mailbox is published, use GitHub's private
vulnerability reporting feature for this repository. Include:

- affected component and revision
- reproduction steps or a minimal proof of concept
- expected impact
- suggested mitigation, if available

Do not access data that does not belong to you, disrupt shared services, or run
destructive tests. We will acknowledge a complete report as soon as practical
and coordinate disclosure after a fix is available.

## Deployment responsibility

Self-hosters are responsible for TLS termination, secret management, network
policy, backups, monitoring, and timely dependency updates. Development defaults
are not a production security profile.

## Container vulnerability policy

CI preserves complete Syft SBOMs and Grype JSON/table reports for every pinned
base, service, and built application image. Fixed High or Critical findings
block a build by default. Unfixed findings remain visible in the uploaded
report rather than being hidden. Narrow exceptions must identify their scope,
reason, and review expiry in
`scripts/security/container-vulnerability-policy.json`; an expired exception
fails CI. The derived API, Web, and Engine runtime images are always evaluated
separately from disposable build stages.
