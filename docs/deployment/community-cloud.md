# Streamlit Community Cloud deployment

## Current status and privacy blocker

The Community Cloud target is `app/Home.py` on Python 3.12. As of August 15, 2026,
`ChimdumebiNebolisa/visa-sponsor-intelligence` is public. Do not deploy or describe the application
as private until the repository owner makes the repository private and the deployed application's
Sharing setting is verified as **Only specific people can view this app**.

Changing the repository to private prevents future unauthenticated access, but it cannot retract
copies of release assets or public forks that already existed. Review the current release contents
before treating them as confidential. GitHub documents the owner-only visibility action and its
consequences in [Setting repository visibility][github-visibility].

## Runtime data contract

Hosted startup uses an authenticated, read-only GitHub Release bootstrap. It downloads exactly:

- `immigration.duckdb`
- `data-quality.json`
- `build-metadata.json`
- `checksums.sha256`

It does not download processed Parquet, build state, source manifests, raw evidence, policy caches,
or ingestion artifacts. The fine-grained token must be limited to this repository, grant only
**Contents: read**, and have an appropriate expiration. Never put it in Git, a URL, or application
logs.

Before a generation becomes current, the bootstrap verifies GitHub's asset digests, release
checksums, the zero-critical-failure quality result, matching release tag/build date/build ID,
nonzero row metadata, the exact V2 metric and score versions, and required V2 DuckDB views and
columns. Downloads go to a locked staging directory. Only a fully verified generation is promoted,
and the `current.json` pointer is replaced atomically.

A previously verified cache generation may be used when GitHub has a temporary transport, rate
limit, or 5xx failure. Authentication errors, missing assets, failed quality gates, invalid hashes,
metadata mismatches, and invalid databases fail closed. Release mode never falls back to an empty
database.

## Community Cloud configuration

Community Cloud searches the entrypoint directory before the repository root for dependency files.
`app/requirements.txt` therefore takes precedence over the full root `uv.lock` and installs the
local project with only its read-only runtime dependencies. Browser automation, OpenAI, PDF parsing,
and ingestion packages remain in the default local `ingestion` uv group and are not installed on the
hosted app. This follows Streamlit's [dependency-file precedence][streamlit-dependencies].

The committed `.streamlit/config.toml` keeps CORS and XSRF protections enabled, disables browser
trace details and telemetry, and uses minimal viewer chrome. The real secret belongs only in
Community Cloud Advanced settings; `.streamlit/secrets.example.toml` is a template.

Required secret values:

```toml
SPONSOR_INTEL_DEPLOYMENT_MODE = "release"
SPONSOR_INTEL_REQUIRE_DATA = true
SPONSOR_INTEL_GITHUB_REPOSITORY = "ChimdumebiNebolisa/visa-sponsor-intelligence"
SPONSOR_INTEL_RELEASE_TAG = "latest"
SPONSOR_INTEL_RELEASE_CACHE_DIR = "/tmp/sponsor-intel-release-cache"
GITHUB_RELEASE_READ_TOKEN = "<fine-grained Contents:read token>"
```

Community Cloud documents secret entry in [Advanced settings][streamlit-deploy] and recommends that
real `secrets.toml` values never be committed in [Secrets management][streamlit-secrets].

## Owner-only deployment steps

1. Review and merge the approved Phase 10 pull request to `main`. Audit the currently public V1
   release contents, then in GitHub **Settings > General > Danger Zone** change repository visibility
   to **Private** and confirm `gh repo view ChimdumebiNebolisa/visa-sponsor-intelligence --json
   visibility` reports `PRIVATE`.
2. On private `main`, manually run **Refresh government sponsorship data**. Confirm its publish job
   creates a new `data-YYYY-MM-DD` release whose `build-metadata.json` reports
   `scored_metrics_v2` and `evidence_scores_v2_2026_08`; do not deploy against the current V1
   release.
3. In Community Cloud **Settings > Linked accounts**, authorize Streamlit to access private GitHub
   repositories. Create a repository-scoped fine-grained token with **Contents: read** only.
4. Create the app from repository `ChimdumebiNebolisa/visa-sponsor-intelligence`, branch `main`,
   entrypoint `app/Home.py`; select Python 3.12, paste the six TOML values above, and set **App
   settings > Sharing** to **Only specific people can view this app**.
5. Reboot the app from a cold state, run the post-deployment checks below as an authorized owner,
   then confirm a signed-out browser and a non-invited account cannot open the URL.

Community Cloud currently permits one private app per account; check the workspace before step 3.
Private-app sharing behavior is documented in [Share your app][streamlit-sharing].

## Post-deployment validation

- The sidebar shows the expected release tag, build ID/date, latest complete fiscal year, and partial
  fiscal-year warning.
- Home, All Employers, Research Institutions, Organization Detail, Compare, Evidence Review, and
  Data Health load nonzero data.
- Employer and institution searches, one detail view, a three-organization comparison, and both
  exports complete successfully.
- `UNKNOWN` is distinct from `NO` and zero; partial FY2026 remains visibly labeled.
- A cold reboot downloads at most the four runtime assets. A warm rerun uses the verified cache.
- Logs contain no token, authorization header, detailed browser traceback, ingestion, Playwright,
  OpenAI, or policy-refresh activity.
- Record clean install duration/size, database and transfer size, cold/warm startup, query latency,
  export latency, and peak runtime memory for the Streamlit retention ADR.

## Clean Linux dependency check

Community Cloud runs on Debian Linux. Validate the deployment dependency path in a clean Python 3.12
environment before owner deployment:

```bash
python3.12 -m venv /tmp/sponsor-intel-deploy
/tmp/sponsor-intel-deploy/bin/pip install --upgrade pip
/tmp/sponsor-intel-deploy/bin/pip install -r app/requirements.txt
/tmp/sponsor-intel-deploy/bin/python -c "import duckdb, httpx, polars, streamlit; import sponsor_intel"
```

The deployment environment must not contain `openai`, `playwright`, `pdfplumber`, `fastexcel`,
`selectolax`, or `xlsx2csv`.

[github-visibility]: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility
[streamlit-dependencies]: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies
[streamlit-deploy]: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
[streamlit-secrets]: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
[streamlit-sharing]: https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app
