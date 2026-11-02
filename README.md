# Hunter

The daily GitHub Actions scan fetches four ATS providers, normalizes full jobs,
deduplicates with SQLite and emails a Gmail digest of new jobs. The original
Telegram pipeline remains legacy/manual only, with separate `seen_jobs.json`
state; no Telegram setup is required for daily email scans.

## How it works

```
sources.yaml -> FETCH -> NORMALIZE -> FILTER -> SQLITE DEDUP -> GMAIL -> COMMIT LEDGER
               (raw)     (Job)                 (ats, ats_job_id)       (after send)
```

Each run emails only jobs passing the location/keyword rules and not already in
`data/seen_jobs.sqlite3`.

## Setup

### 1. Set the GitHub Secrets

In the repo: Settings -> Secrets and variables -> Actions -> New repository secret.

| Secret | Value |
| --- | --- |
| `GMAIL_ADDRESS` | Sender's Gmail address (single bare email address) |
| `GMAIL_APP_PASSWORD` | Google app password for that account, not its normal password |
| `NOTIFY_TO` | Recipient's email address (single bare email address) |

Secrets are injected into runs as environment variables.
Never put them in `sources.yaml`, code, logs, artifacts or any tracked file.
Enable Google two-step verification and create an app password if your account
supports it. Whitespace in copied app passwords is removed before authentication.
Revoke and replace any app password shared in chat or accidentally committed.

### 2. Add sources

Edit `sources.yaml`.
Adding a company on a supported ATS is one entry, for example:

```yaml
sources:
  - type: ashby
    company: wealthsimple
```

## Running locally

```bash
pip install -r requirements.txt
python -m scraper.main --dry-run
```

`--dry-run` prints would-be notifications to stdout instead of sending to Telegram; everything before the notify stage behaves exactly like a real run.

The command above is the **legacy** pipeline. For the canonical Gmail pipeline,
configure the three environment variables securely in your local session, then:

```powershell
python -m ingest --sources ingestion_sources.yaml --send-email
```

Omit `--send-email` for local ingestion without SMTP; this still records jobs as
seen. Those jobs will not subsequently be emailed by enabling the flag. Keep the
existing ledger rather than deleting it just to trigger a test email.

## Phase 1: raw ATS fetching

The watchlist covers all four ATS providers: 73 Greenhouse,
4 Lever, 63 Ashby and 58 Workday source entries. This is a configured watchlist,
not automatic discovery of every company using those providers. The existing
notification filters target Canada, selected engineering/internship titles and
the last three days, and exclude senior/leadership titles. These filters do not
apply to the raw fetchers.

Each module in [fetchers](fetchers) exposes `fetch(company: dict) -> list[dict]`.
Only the four international providers are implemented; the other two providers
are deferred until specified. No authentication or Telegram configuration is
needed. Raw fetching does not write dedup state, filter postings, or send messages.

| Provider | Raw fetch behavior |
| --- | --- |
| Greenhouse | `content=true`; decode HTML entities once, retaining full HTML and all fields |
| Lever | Entire bare JSON list, including any workplace/salary fields provided |
| Ashby | All job records, including unlisted records, full descriptions, `workplaceType` and available compensation |
| Workday | POST pages of 20; GET each `externalPath` for the complete detail payload |

Greenhouse shares a process-wide request-start limiter (at least 0.25 seconds
between starts) and a maximum of four boards in flight, including calls from the
notification adapter. Do not run multiple independent processes if you need that
aggregate limit across a whole run.

All HTTP calls use a 30-second timeout and check HTTP status. Malformed payloads
and failed detail requests raise errors rather than looking like empty results.
The existing pipeline supplies its retry policy; direct raw fetcher callers get
the exception.

Run these commands from the project root to print a real job JSON array:

```powershell
python -m fetchers greenhouse --slug duolingo
python -m fetchers lever --slug palantir
python -m fetchers ashby --slug wealthsimple
python -m fetchers workday --host paypal.wd1.myworkdayjobs.com --tenant paypal --site jobs --max-jobs 21
```

By default, the first fetched job is printed, without truncating its fields.
Use `--sample 0` to print all fetched jobs; `--sample N` only changes output size,
not how many jobs are fetched. Counts and warnings go to stderr, while stdout
contains only JSON. Empty boards and HTTP/schema failures exit nonzero so they
cannot pass live acceptance accidentally. A board can be legitimately empty or
abandoned after an ATS migration; verify its current Apply links.

For Workday, `--max-jobs` limits fetched records and detail requests (1-2000,
default 2000). List requests always use `limit: 20` and `appliedFacets: {}`.
Pagination stops on a short page or the first page's positive total; later pages'
occasionally incorrect `total: 0` is ignored. Offsets never reach 2000. A configured
or safety cap, or a short page before the reported total, logs a warning: results
may be incomplete, not a full-board guarantee. Both list and detail requests send
`User-Agent` and the site's `Referer`.

The Workday raw result preserves every list field and adds a `detail` object
containing the untouched detail response. Its complete HTML JD is at
`record["detail"]["jobPostingInfo"]["jobDescription"]`.

```python
from fetchers import get_fetcher

jobs = get_fetcher("greenhouse")({"slug": "duolingo"})
jobs = get_fetcher("workday")({
    "host": "paypal.wd1.myworkdayjobs.com",
    "tenant": "paypal",
    "site": "jobs",
    "max_jobs": 21,
})
```

The existing `company` key is also accepted instead of `slug`. Workday accepts
the existing `company`/`instance`/`site` source configuration as well as explicit
`host`/`tenant`/`site`. An optional `search` value becomes `searchText`, defaulting
to the empty string.

Greenhouse, Lever and Ashby notification adapters reuse the raw fetchers, then
retain their original `Job` conversion, 500-character summaries and skip rules.
The notification Workday adapter intentionally remains the original lightweight
three-page/no-detail poller; use `fetchers.workday` for the fuller Phase 1 fetch.
No existing filters or notification settings have been changed.

If a corporate HTTPS proxy causes a certificate-chain failure, set
`REQUESTS_CA_BUNDLE` to an approved PEM CA bundle that includes the corporate CA.
Keep TLS verification enabled; do not use `verify=False`.

## Phases 2 and 3: canonical jobs and the SQLite seen ledger

The [normalizers](normalizers) package provides a separate
`normalize(record: dict, company: dict) -> Job` for each of the four raw fetchers.
The [canonical Job](normalizers/models.py) has exactly these fields:
`ats`, `ats_job_id`, `company`, `title`, `location`, `description`, `apply_url`,
`posted_at`, `department=None`, and `remote=None`.

Descriptions are complete plain text, not 500-character summaries. HTML text
entities are decoded while removing markup; Greenhouse's outer HTML escaping
is decoded **once by its fetcher**, not again before parsing in its normalizer.
Pass fetcher output (not Greenhouse's escaped wire content) to `normalize`.
Lever's description includes its lists and closing sections. Official URLs come
from Greenhouse `absolute_url`, Lever `hostedUrl` (then `applyUrl`), Ashby `jobUrl`
(then `applyUrl`), or the Workday host/site/externalPath URL.

`posted_at` is an aware UTC `datetime` or `None`. Sources are Greenhouse
`first_published`/`first_published_at` (never `updated_at`), Lever `createdAt`
milliseconds, Ashby `publishedAt`, and Workday detail `jobPostingInfo.startDate`.
Date-only and timezone-free dates are interpreted as UTC. Missing dates stay
`None`; invalid, year-one and zero/pre-epoch dates log a warning and become
`None`. Relative Workday labels such as "Posted Today" are not converted using
the current clock. Only ledger observation timestamps use the current time.

`remote=True` means explicitly Remote; Hybrid/OnSite map to `False`. Unknown
workplace types stay `None`, with Ashby's structured `isRemote` as a fallback.
No keyword guessing from titles or locations is performed. Company display name
uses `name`, then `company`, `slug`, or `tenant`. Missing optional departments
remain `None`.

[ledger.py](ledger.py) stores only:

```text
seen(ats, ats_job_id, company, title, first_seen, last_seen)
PRIMARY KEY (ats, ats_job_id)
```

IDs are strings. Workday uses top-level/detail `jobPostingId`, then `externalPath`.
Do not deduplicate on titles, URLs, tracking parameters or company names.
Repeated sightings preserve `first_seen`, refresh `last_seen` and current
company/title metadata, and are not returned as new. Duplicates within a batch
also return only once. Writes are transactional, with an atomic conflict-safe
insert so concurrent processes cannot both report the same ID as new.

Run the bounded four-provider sample from the project root:

```powershell
# Live run: normalize and record all fetched jobs; save a repeatable raw snapshot.
python -m ingest --sources ingestion_sources.yaml --snapshot data\raw_jobs.json

# Independent local run: replay exactly the same jobs against the same ledger.
python -m ingest --input data\raw_jobs.json
```

With an empty ledger, the first run's `new` count equals its unique matched job count.
The second prints `"new": 0` and `"jobs": []`. Both print a JSON object with
`fetched`, `matched`, `rejected`, `new`, `db` and the newly seen canonical `jobs`; dates serialize as
ISO strings or JSON `null`. Counts/warnings/errors go to stderr. You can repeat
the first live command instead; it also returns zero new jobs unless the actual
boards have changed in between.

The raw snapshot contains `{"batches": [{"source": {...}, "jobs": [...]}]}`.
Its records are fetcher output, including Greenhouse's decoded HTML and Workday's
detail object. Replay needs no network. Use `--db PATH` to select a different
ledger; the default is `data\seen_jobs.sqlite3`, relative to the project root.
The [sample sources](ingestion_sources.yaml) fetch all jobs from three boards
and only 21 Workday jobs to exercise pagination without fetching hundreds of
details. The normal Workday safety cap is still 2000.

`--sources sources.yaml` instead polls the full existing watchlist for the four
supported ATS types, ignoring other types **with warnings**. Location and keyword
rules are applied by default; no age/seniority filter is added. All records must normalize successfully
before the batch reaches SQLite; a fetch/normalization/database failure exits
nonzero, not a success-shaped empty result. Existing ledger rows are never
automatically pruned or reset.

**Back up the ledger.** It is the durable dedup asset; `data/seen_jobs.sqlite3`
is allowlisted for Git so Actions can persist it. Other local databases and raw
snapshots remain ignored. Use SQLite's backup API for a running database, or copy it only
after all connections are closed. Losing/replacing it makes existing postings
appear new. Never delete it merely to rerun an acceptance check. Under this
requested two-column key, IDs must be unique within an ATS; if a future provider
has board-local IDs, its identity policy needs an explicit migration, not a
silent change to include company/title/URL.

This ingestion ledger is independent of the original Telegram `seen_jobs.json`.
Without `--send-email` it records observations only. With email enabled it commits
new sightings only after the Gmail digest is accepted. Original Telegram filters
and JSON state are not used or changed by the canonical pipeline.

## Job matching: Hong Kong, Shanghai and Shenzhen

[job_filters.yaml](job_filters.yaml) is the default rules file for both local
ingestion/replay and GitHub Actions, including smoke and scheduled scans.

1. The canonical `Job.location` must contain Hong Kong / 香港, Shanghai / 上海,
   or Shenzhen / 深圳. Multiple locations pass if any target city appears.
   Unknown locations, bare "China"/"Remote", and cities mentioned only in the JD
   do not qualify. Only locations preserved by the normalizer are searchable;
   this does not infer a city from Workday's "2 Locations".
2. Exclude matches in company, title, department or full JD take priority:
   外包, 派遣, staffing, recruiter, internship program (also programme).
   Even incidental mentions such as "no staffing agencies" reject a posting;
   these are literal exclusion rules, not semantic interpretation.
3. Require at least one include keyword in title, department or full JD.
   Company names alone cannot trigger an include. The file contains the requested
   AI/Algorithm/new-grad/developer/agent/LLM/RAG/harness/tool-use/workflow/planning
   terms and related artificial intelligence, machine/deep learning, agentic,
   tool/function calling, embodied and graduate variants.

Matching uses Unicode NFKC, casefolding, whitespace normalization, explicit
keyword-relevant traditional-to-simplified character mapping (開發/應用/智能體/
機器學習/大語言模型/應屆畢業生/規劃, etc.), and Chinese substring matching.
It is a deterministic keyword list, not a general Chinese translation engine or
LLM classifier. English terms use letter/digit boundaries (`AI` does not match
`retail`/`detail`, `RAG` does not match `ragtime`); multiword terms accept whitespace
or hyphens, including `tool-use` and `retrieval-augmented generation`.

The pipeline is **normalize -> filter -> SQLite dedup -> Gmail**. Only matching
jobs enter the ledger and email; rejected postings can qualify on a future run
if their content or rules change. Existing historical ledger entries are kept
and never automatically resent when rules change. No matching unseen jobs means
no SMTP call. Sending failures still roll back the matched batch.

JSON/logs report `fetched` (before filtering), `matched` (before dedup), `rejected`
(counts by `location`, `excluded`, `keyword` in that priority order), and `new`
(matching unseen jobs). A zero-new run need not be zero-matched.

Use `--filters PATH` for a custom rules file, or `--no-filters` to explicitly
restore unfiltered ingestion. Invalid/missing rules fail rather than silently
disabling filtering. The raw `python -m fetchers` interface and original legacy
Telegram filters remain unchanged. No salary, age or seniority restrictions
were added.

## Phase 5: daily GitHub Actions scan

[daily-scan.yml](.github/workflows/daily-scan.yml) runs at `17 1 * * *`:
UTC 01:17 / Beijing (Asia/Shanghai) 09:17 every day. It also exposes
**Actions -> Daily Job Scan -> Run workflow** on the default branch.
Manual dispatch defaults to `scan_scope: smoke`, using `ingestion_sources.yaml`
to verify four live boards, email and ledger persistence first. Choose `full`
for the entire watchlist. Scheduled runs always use the full `sources.yaml`.
The first full cloud run found a 404 for Greenhouse `marqeta`; that source now
uses Ashby `marqeta-inc`. Other invalid/migrated sources still fail explicitly.
Postman is excluded from this target-city watchlist rather than repairing its
obsolete Greenhouse source. The remaining inherited companies have not all been
audited for current Hong Kong, Shanghai or Shenzhen openings.
Smoke success does not certify the health of the full watchlist.
It calls the real entry point, `python -m ingest --sources sources.yaml`,
not a nonexistent `main.py`.

The workflow uses Ubuntu, Python 3.12 and pip caching. It scans the **full existing
watchlist for the four supported ATS types**, not just the four-board smoke
configuration. Each Workday board can fetch up to 2000 jobs plus per-job details;
runtime/request counts can therefore be much larger than the local 419-job
acceptance. Do not assume it always takes only a few minutes or fits a private
repository's included allowance. The job timeout is 180 minutes; reaching it is
a failure, not a successful partial scan.

The original [poll.yml](.github/workflows/poll.yml) is now **manual only**, so its
five-minute trigger and twenty-minute polling loop do not run alongside the daily
scan. Its Telegram behavior is otherwise unchanged. Both workflows share the
`hunter-state` concurrency group without cancelling an active run.

### Activation

1. Publish this project to a repository you own (or have write access to), with
   the workflow on its default branch. The downloaded ZIP is not a Git clone;
   local changes alone do not enable a remote schedule. Avoid publishing the
   unrelated parent workspace.
2. Commit `data/seen_jobs.sqlite3` together with the code to preserve the local
   already-seen baseline. It is now explicitly allowed by `.gitignore`; no
   force-add is required. Review the public ledger's contents first: it contains
   company/title/ATS IDs and observation timestamps, **not secrets or full JDs**.
   The workflow also handles a deliberate first run without a ledger by creating
   and committing one after a successful scan.
3. Enable Actions, allow the referenced actions, and permit the workflow's
   `contents: write` token to push to the default branch. Branch protection or
   organization policy may block direct bot pushes; configure a permitted
   persistence policy rather than making failures invisible.
4. Manually run **Daily Job Scan** and verify the scan result, ledger commit and
   push before relying on cron.

The daily scan passes `--send-email` and requires the three Gmail Secrets listed
above. Missing settings fail before any fetching or ledger updates; Telegram
Secrets are not read by this workflow.

### Ledger persistence and failure handling

After a successful scan sends its Gmail digest and closes SQLite, the commit step verifies database
integrity/schema, stages **only** `data/seen_jobs.sqlite3`, skips a genuinely
unchanged ledger, and commits/pushes it to the default branch. Checkout on the
next run restores that exact ledger, so dedup history survives runner disposal.
`last_seen` changes normally produce a commit even if the new-job count is zero.

Fetch/normalize failures do not run the commit step. Missing/corrupt ledgers,
commit errors and rejected pushes fail the workflow; there is no `|| true`,
force-push, hard reset or automatic binary SQLite merge. If another actor pushes
during the scan, a rejected push remains visible and must be reconciled rather
than silently dropping this run's observations.

An always-run artifact step retains `scan-result.json` and the available SQLite
ledger for seven days, including after a rejected push. Download the recovery
ledger before manually reconciling a failed persistence run; do not blindly
overwrite a newer ledger. The default-branch ledger is the source of truth, not
an evictable Actions cache or an expiring artifact. Scan results include full
new-job descriptions and may be downloadable by other repository users; keep
credentials and personal data out of source configurations and artifacts.

### Scheduling and billing caveats

- Schedules run from the default branch. Cron's minimum interval is five minutes.
  Using minute `17` avoids the top-of-hour peak, but runs can be delayed or even
  dropped under load; **3-10 minutes is not a guaranteed delay bound**.
- Public repositories with no activity for 60 days can have schedules disabled;
  check the Actions UI and re-enable them when necessary. Forks may need
  scheduled workflows enabled explicitly.
- Standard GitHub-hosted runners are free for public repositories. Private
  repositories can also run schedules on GitHub Free under applicable included
  usage and repository/organization policies: **GitHub Pro is not universally
  required for private scheduled workflows**. Legacy per-repository plans are
  a separate exception. Check current plan allowances and billing rather than
  relying on a fixed historic minute allowance or Pro price.
- Artifacts have storage allowances/billing separate from runner compute.

Official references:
[schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule),
[Actions availability](https://docs.github.com/en/actions/concepts/billing-and-usage#availability),
[billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## Gmail delivery

[email_notify.send_email](email_notify.py) uses only standard-library `smtplib`,
`ssl` and `EmailMessage`: `smtp.gmail.com:587`, a 30-second timeout, verified
STARTTLS, then login and send. It never uses plaintext authentication before TLS
or disables certificate checks. Credentials come exclusively from environment
variables, never from a tracked configuration file. SMTP debug logging is not
enabled, and authentication/server exception details are not printed.

Zero new jobs means **no email** and no SMTP connection. Otherwise one UTF-8
plain-text digest contains the count and the first 30 jobs' company, title,
location and official apply URL. If there are more than 30, the email explicitly
points to the scan's full JSON output/artifact for the remainder. All jobs in that
digest batch are marked seen, including those omitted from the body.

`--send-email` validates settings before fetching and sends within the SQLite
batch transaction, before commit. TLS/login/send/refusal failures roll back the
batch and exit nonzero, keeping those jobs eligible for retry. The workflow does
not commit the ledger on failure. Successful repeat scans with the same jobs
produce zero new jobs and no second email.

SMTP delivery and SQLite/Git commits cannot form one distributed transaction:
if Gmail accepts a message but the connection, SQLite commit or Git push later
fails, a retry may send a duplicate. This intentionally prefers retrying over
silently losing a notification; it is **not exactly-once delivery**. Existing
observation-only ledger entries are not automatically resent. The persisted
419-job local acceptance baseline remains already seen.

## Development

```bash
ruff check .   # lint
pytest         # tests run fully offline (HTTP is mocked)
```
