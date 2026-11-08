# Security policy

Before 1.0, security fixes target the latest minor release. Update to the latest
patch release within that line and review the changelog when upgrading.

Report a vulnerability privately through
[GitHub's vulnerability reporting page](https://github.com/akaliutau/skyrun/security/advisories/new)
when private reporting is enabled. Otherwise, contact the repository owner using
the contact details on [their GitHub profile](https://github.com/akaliutau).
Include affected versions, reproduction steps, and the practical impact. Avoid
posting credentials, private code, or an unpatched exploit in a public issue.

Skyrun executes commands you provide, using your cloud account and selected
provider secrets. Treat workload code and dependencies as trusted code. Default
ignore rules reduce accidental uploads but cannot identify every private file.
Review `--dry-run`, keep secrets outside the project, and use named Modal Secrets
for values that must be available remotely. Environment values configured in
plain TOML and anything printed by the workload are retained in run records/logs.

Path checks reject traversal and symlink destinations, and selected-file sync
uses content hashes and atomic replacements. These checks are not an operating
system sandbox against another local process that can mutate the same
directories concurrently. Keep the project and download roots under your own
control and avoid concurrent filesystem mutation while applying recovered files.
