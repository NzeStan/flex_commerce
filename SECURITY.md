# Security policy

## Reporting a vulnerability

Please **do not** open a public issue. Email **nnamaniifeanyi10@gmail.com** with a description, affected
versions and steps to reproduce. You will get an acknowledgement within 72 hours and a fix or mitigation plan
as quickly as possible. Credit is given in the changelog unless you prefer otherwise.

## Supported versions

| Version | Supported |
|---|---|
| 1.x | ✅ |
| Pre-release (GitHub, before 1.0.0) | ❌ — upgrade to 1.0.0 |

## Design

See [docs/operations.md](docs/operations.md#security-notes) for the protections built into FlexCommerce
(server-side pricing, payment verification, webhook signing and SSRF protection, object-level permissions,
throttling, CSV injection protection).
