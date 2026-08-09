# Third-party notices

Skeinix's original source code is licensed under the Apache License 2.0. The
project also uses third-party packages that remain governed by their own
licenses. Nothing in Skeinix's `LICENSE` file replaces those terms.

The committed Python and pnpm lockfiles identify exact package versions. Release
images additionally carry SPDX SBOM attestations produced by the release
workflow. License texts and copyright notices distributed inside upstream
packages must be preserved when redistributing those packages or derived binary
artifacts.

Notable license choices in the current dependency graph include:

- The Docker stack uses Valkey, a Redis-protocol-compatible datastore released
  under the BSD 3-Clause license, instead of Redis releases under RSALv2/SSPLv1.
- `elkjs` is available under `EPL-2.0 OR GPL-3.0-or-later`; Skeinix uses it
  under EPL-2.0.
- `jszip` is available under `MIT OR GPL-3.0-or-later`; Skeinix uses it under
  the MIT license.
- `axe-core`, `lightningcss`, `certifi`, `orjson`, and `tqdm` include MPL-2.0
  terms, alone or together with permissive alternatives.
- `psycopg`, `psycopg-binary`, and `psycopg-pool` are unmodified Python
  dependencies under LGPL-3.0-only.
- `pytest-postgresql` and `mirakuru` are development/test dependencies and are
  not part of the production runtime image.

Before publishing a binary distribution, review the generated SBOM and license
inventory for that exact release. Dependency updates can change this list even
when application code is unchanged.
