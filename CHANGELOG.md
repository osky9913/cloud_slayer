# Changelog

All notable changes to cloudslayer are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [0.3.0] — 2026-07-08

**Refocus: AWS, GCP, and Azure.** cloudslayer now concentrates on deep, live-priced
coverage of the three major clouds instead of broad coverage of many.

### Added
- **Terraform plan JSON support** — `cloudslayer scan|compare|analyze plan.json` reads
  `terraform show -json` output, so modules, variables, `count` and `for_each` are
  fully resolved (raw `.tf` scanning remains the zero-setup fallback)
- **Coverage honesty line** — scan/compare/analyze now report exactly how many
  detected resources are costed, not costed yet (listed individually), or have no
  direct cost — estimates are never silently incomplete
- Detection (not yet costed) for 20+ additional cost-relevant resource types:
  NAT gateways, load balancers, EBS/managed/persistent disks, EKS/GKE/AKS
  clusters and node groups, ElastiCache/Memorystore/Redis Cache, DynamoDB,
  CloudFront
- **Azure Database for PostgreSQL (Flexible Server) provider** — live pricing via
  the Azure Retail Prices API, 11 compute tiers; completes the big-3 database matrix
- `--format markdown` on `plan` — GitHub-flavored tables for PR comments
- **TUI polish** — summary strip with headline numbers, proportional savings bars
  per strategy, `j`/`k` navigation, `1`/`2`/`3` tier jumps, light/dark toggle (`t`),
  cheapest-combination total bar in plan view, SVG screenshots in the README
- Release automation: PyPI publishing via GitHub Actions trusted publishing
- Dockerfile and reusable GitHub Action
- `CODE_OF_CONDUCT.md` and `SECURITY.md`

### Removed
- Non-big-3 providers (Hetzner, DigitalOcean, Vultr, Akamai/Linode, Cloudflare R2,
  Backblaze B2, Wasabi, Neon, Supabase, Cloudflare Workers) — refocused on
  AWS/GCP/Azure where pricing can be verified from official APIs

---

## [0.2.0] — 2026-07-03

### Added
- `cloudslayer diff before.hcl after.hcl` — compare cost delta between two specs (CI-friendly)
- Azure VM compute provider (East US, 12 instance types)
- Akamai/Linode compute provider (11 instance types including Dedicated CPU)
- DigitalOcean Managed PostgreSQL database provider (6 plans)
- `--top N` flag on `plan` — show only the N cheapest providers per resource
- `--provider` flag on `plan` and `diff` — filter to specific providers (e.g. `--provider hetzner,aws`)
- `--fail-if-over AMOUNT` flag on `plan` and `diff` — exit code 2 if cheapest combo exceeds budget (CI budget gates)
- `cloudslayer cache status` / `cloudslayer cache clear` commands
- `cloudslayer version` command
- Rich progress spinner during live API calls
- Recommended stack table in total summary (shows cheapest provider per resource)
- GitHub Actions CI workflow (`.github/workflows/ci.yml`) — tests on Python 3.10–3.13
- GitHub issue templates (bug report, feature request)
- GitHub PR template with provider pricing checklist
- `CONTRIBUTING.md` with full provider authoring guide
- `[project.optional-dependencies]` dev extras in pyproject.toml

### Changed
- `cloudslayer init` now generates a `diff`-based GitHub Actions workflow (shows cost delta on PRs instead of just current costs)
- `render_total_summary` now includes a "Recommended stack" breakdown table
- `cloudslayer providers` output now shows pricing source type (live vs verified date)

### Fixed
- `dsl.py`: `engine` string attribute was not having quotes stripped (python-hcl2 wraps string values in quotes)

---

## [0.1.0] — 2026-07-03

### Added
- Initial release
- `cloudslayer plan <file.hcl>` — compare costs across providers
- `cloudslayer scan <dir>` — detect Terraform resources
- `cloudslayer scan <dir> --generate-spec` — generate HCL spec from Terraform
- `cloudslayer init` — generate GitHub Actions workflow
- `cloudslayer providers` — list all providers and pricing
- Object storage: AWS S3 (live), Azure Blob (live), GCP Cloud Storage, Cloudflare R2, Backblaze B2, Wasabi, Hetzner Object Storage
- Compute: AWS EC2, GCP Compute Engine, Hetzner Cloud, DigitalOcean, Vultr
- Database: AWS RDS, GCP Cloud SQL, Supabase, Neon
- HCL2 spec DSL (`object_storage`, `compute`, `database` blocks)
- JSON output format (`--format json`)
- Apache 2.0 license
