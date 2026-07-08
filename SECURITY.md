# Security Policy

## Supported versions

Only the latest released version of cloudslayer receives security fixes.

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Report privately via [GitHub Security Advisories](https://github.com/cloudslayer-dev/cloudslayer/security/advisories/new)
or email **m.osvald9913@gmail.com**. You can expect an initial response within 7 days.

## Scope notes

cloudslayer is a read-only cost estimation tool. It never modifies cloud resources. The
`actual` command reads billing/inventory data using your local cloud credentials;
credentials are handled by the official SDKs (boto3, google-cloud, azure-identity)
and are never stored or transmitted by cloudslayer. Pricing data is fetched from public,
unauthenticated pricing APIs and cached in `~/.cloudslayer/cache/`.
