# Product A Streamlit Community Cloud deployment

## Current blocker

The target is `app/Home.py` on Python 3.12, but
`ChimdumebiNebolisa/visa-sponsor-intelligence` is currently public. **Do not publish a new data
release or deploy/describe the application as private while the repository is public.** Release
assets inherit repository visibility.

Making the repository private later prevents future unauthenticated access; it cannot retract
release assets, clones, forks, or copies that were already public. The owner must audit existing
release contents before treating any data as confidential. GitHub documents the consequences in
[Setting repository visibility][github-visibility].

Repository visibility, PR merge, release publication, Community Cloud authorization/secrets,
sharing controls, and hosted validation are owner actions. The Product A implementation and
acceptance runner must not perform or report these actions as complete.

## Runtime data contract

Hosted startup uses an authenticated, read-only GitHub Release bootstrap and downloads exactly:

- `immigration.duckdb`
- `data-quality.json`
- `build-metadata.json`
- `checksums.sha256`

It does not download raw evidence, processed Parquet, build state, source manifests, supplemental
policy caches, or ingestion artifacts. A fine-grained token must be restricted to this repository,
grant only **Contents: read**, and have an appropriate expiration. Never put it in Git, a URL, or
logs.

Before promotion, the bootstrap verifies GitHub asset digests, `checksums.sha256`, a current
zero-critical-failure Product A quality result, matching release tag/build date/build ID, nonzero
employer and institution metadata, `product_a_metrics_v1`, `product_a_scores_v1`, and required
Product A DuckDB views/columns. Downloads go into a locked staging generation; only a fully
verified generation replaces the atomic current pointer.

A previously verified cache generation may be used only for a temporary GitHub transport,
rate-limit, or 5xx failure. Authentication errors, missing assets, failed quality, invalid hashes,
metadata mismatches, empty data, or invalid database contracts fail closed. Release mode never
falls back to an empty or fixture database.

## Community Cloud configuration

Community Cloud looks in the entrypoint directory before the repository root for dependency files.
`app/requirements.txt` therefore defines the lean read-only runtime. Ingestion, browser automation,
OpenAI, and PDF tooling are not required by the hosted app.

The committed `.streamlit/config.toml` keeps CORS and XSRF protections enabled, disables telemetry
and detailed browser traces, and uses minimal viewer chrome. Real secrets belong only in Community
Cloud Advanced settings; `.streamlit/secrets.example.toml` is a template.

Required secret values after the owner establishes a private release:

```toml
SPONSOR_INTEL_DEPLOYMENT_MODE = "release"
SPONSOR_INTEL_REQUIRE_DATA = true
SPONSOR_INTEL_GITHUB_REPOSITORY = "ChimdumebiNebolisa/visa-sponsor-intelligence"
SPONSOR_INTEL_RELEASE_TAG = "data-YYYY-MM-DD"
SPONSOR_INTEL_RELEASE_CACHE_DIR = "/tmp/sponsor-intel-release-cache"
GITHUB_RELEASE_READ_TOKEN = "<fine-grained Contents:read token>"
```

Pin an audited Product A release tag rather than deploying an unknown historical `latest`. Community
Cloud documents [Advanced settings][streamlit-deploy] and [Secrets management][streamlit-secrets].

## Owner-only sequence

1. Review and merge the approved Product A pull request to `main`.
2. Audit/delete or retain with explicit understanding any existing public release assets. In GitHub
   **Settings > General > Danger Zone**, change visibility to **Private**, then confirm:

   ```bash
   gh repo view ChimdumebiNebolisa/visa-sponsor-intelligence --json visibility
   ```

   The result must be `PRIVATE` before any new data release is published.
3. On private `main`, run the protected government-data refresh. Confirm quality and Product A
   acceptance pass without an OpenAI key or policy state.
4. Publish the verified `data-YYYY-MM-DD` bundle. Inspect `build-metadata.json` for nonzero counts,
   `product_a_metrics_v1`, and `product_a_scores_v1`; do not deploy a Phase 10/V2 or historical V1
   release.
5. Authorize Streamlit to access the private repository. Create a repository-scoped fine-grained
   **Contents: read** token.
6. Create the app from `main`, entrypoint `app/Home.py`, on Python 3.12. Enter the six secrets above
   and set **Sharing** to **Only specific people can view this app**.
7. Cold-reboot the app, complete post-deployment validation as an authorized owner, then verify a
   signed-out browser and a non-invited account cannot access it.

Community Cloud private-app availability and limits can change; verify the owner's current plan and
workspace before deployment. Private sharing is documented in [Share your app][streamlit-sharing].

## Post-deployment validation

- Sidebar/Home show the pinned release tag, Product A build/score versions, build date, source
  freshness, latest complete FY, and current partial warning.
- Home, All Employers, Universities and Research Institutions, Organization Detail, Compare, and
  Data Health load nonzero real rows.
- Employer/institution searches, a legal-versus-parent drilldown, a three-organization comparison,
  and filtered exports complete successfully.
- Stars and accessible labels agree; validated zero is `No observed … history`; missing/invalid is
  `Unrated`; zero is never one star.
- The institution default ranking follows sponsorship history, and Research Scale/policy/E-Verify
  cannot alter sponsorship stars.
- USCIS uses the exact label `Employer-level H-1B initial approvals`; partial periods are visible.
- A cold reboot downloads at most the four runtime assets. A warm rerun uses the verified cache.
- A temporary GitHub transport failure uses only the last verified generation; invalid credentials,
  checksums, quality, versions, or data fail closed.
- Logs contain no release token, authorization header, detailed traceback, ingestion, Playwright,
  OpenAI, or policy-refresh activity.
- Record clean-install duration/size, transfer/database size, cold/warm startup, query/export
  latency, peak memory, desktop/basic-mobile usability, and access-control results.

## Clean Linux dependency check

Community Cloud runs on Debian Linux. Before owner deployment, validate the lean dependency path in
a clean Python 3.12 environment:

```bash
python3.12 -m venv /tmp/sponsor-intel-deploy
/tmp/sponsor-intel-deploy/bin/pip install --upgrade pip
/tmp/sponsor-intel-deploy/bin/pip install -r app/requirements.txt
/tmp/sponsor-intel-deploy/bin/python -c "import duckdb, httpx, polars, streamlit; import sponsor_intel"
```

The deployment environment should not contain ingestion-only packages such as `openai`,
`playwright`, `pdfplumber`, `fastexcel`, `selectolax`, or `xlsx2csv`.

## Rollback and incident behavior

- Keep the previously verified release tag available until the new generation passes hosted
  validation.
- On a bad new release, repin `SPONSOR_INTEL_RELEASE_TAG` to the last verified private Product A
  release and reboot; do not bypass checksum/version/quality verification.
- On token exposure, revoke the token immediately, rotate it in Community Cloud, audit logs and
  release access, and cold-reboot.
- On accidental public visibility, stop publication/deployment, restore private visibility, rotate
  credentials, and assume already-public assets may have been copied.

[github-visibility]: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility
[streamlit-dependencies]: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies
[streamlit-deploy]: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
[streamlit-secrets]: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
[streamlit-sharing]: https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app
