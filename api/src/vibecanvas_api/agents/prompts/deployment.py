"""DEPLOYMENT command-context block — published Workflow entry points."""

DEPLOYMENT = """\
## Deployment mode

Use Deployments for published Workflow entry points. List or inspect the exact
deployment before changing or deleting it; never guess ids. A deployment has
one immutable trigger type (`api`, `webhook`, or `cron`) and immutable slug and
Workflow identity. Create a replacement when those identities must change.

Choose `version_pin=head` when the endpoint should follow the Workflow head, or
`specific` with both major and subversion for a stable release. Cron triggers
require a five-field expression and IANA timezone. API/webhook creation may
return a credential only once: clearly surface it to the user without copying
it into logs, files, or later context. Rate limits are protection boundaries,
not throughput promises.

Create, update, disable, and delete are persistent platform changes. Preserve
the normal approval and authorization gates and describe the effective target
and version before a consequential mutation.
"""
