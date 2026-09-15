# YC Job Apply Automation — Current Plan

Human-gated pipeline for [Work at a Startup](https://www.workatastartup.com): scrape → score → draft → email digest → Approve/Reject → submit (Approve only).

Zero-cost stack: GitHub Actions + Cloudflare Workers + SQLite-in-repo + Gmail SMTP + Gemini free tier (OpenRouter `:free` fallback).

**This document matches the shipped code.** Day-to-day setup lives in `README.md`. Deeper product notes: `docs/YC-auto-apply-product-guide.docx`.

Automated apply may conflict with the site’s ToS. Keep the approval gate and daily cap. Never fully autonomous submit.

---

## 1. Repo structure

```
Yc-auto-apply/
├── .github/
│   ├── actions/setup-cached-python/  # restore .venv (+ Playwright) or pip install on miss
│   ├── actions/publish-recording/    # ffmpeg merge, mp4 artifact, GitHub Release, prune, email
│   └── workflows/
│       ├── scan.yml          # every 4h: login → scrape → match → draft → digest → dashboard → commit
│       │                     # follow-on spectate job (no jobs-db lock)
│       ├── submit.yml        # repository_dispatch: approve submit / reject mark + spectate notify
│       ├── report.yml        # ~10pm IST: daily report email
│       ├── resume-otp.yml    # repository_dispatch: email resume-login OTP
│       └── prune-recordings.yml  # hourly: delete recording-* older than 24h
├── worker/
│   ├── index.js              # Router: / approve, /resumes, /resumes/jobs, forgot
│   ├── common.js             # Shared login cookie + HTML helpers
│   ├── auth.js               # KV credentials, session nonce, OTP
│   ├── forgot.js             # Forgot password/name OTP flow
│   ├── approve.js            # HMAC verify → GitHub repository_dispatch
│   ├── resumes.js            # Login + editor; commits data/resumes/*.txt
│   ├── jobs.js               # Read-only DB viewer (paginated jobs.json)
│   ├── jobs_query.js         # Filter / sort / 10-per-page slice
│   ├── jobs_ui.js            # Jobs table + pagination
│   ├── jobs_receipt.js       # Detail receipt sections (score, resume snapshot, timeline)
│   └── wrangler.toml
├── src/
│   ├── login.py              # YC email/password login (cookies fallback)
│   ├── session.py            # Playwright browser/context lifecycle (record after login)
│   ├── record_video.py       # Merge .webm → mp4, object keys, manifest
│   ├── publish_release.py    # Attach mp4 to recording-* GitHub Release
│   ├── prune_recordings.py   # Delete recording-* older than retain_hours
│   ├── spectate_email.py     # Scan recording recap email
│   ├── scrape.py             # Walk search.sources; parse Algolia / fetch / Inertia
│   ├── waas_parse.py         # Listing JSON → company/role/url/jd helpers
│   ├── match.py              # TF-IDF + keyword overlap + hard filters
│   ├── draft.py              # LLM notes + validation retries
│   ├── llm.py                # Gemini primary, OpenRouter fallback (httpx)
│   ├── email_digest.py       # HTML digest + HMAC Approve/Reject links
│   ├── tokens.py             # Sign/verify approval tokens
│   ├── submit.py             # Apply → fill note → Send (or --dry-run)
│   ├── notify.py             # Per-job confirmation email
│   ├── daily_report.py       # 10pm→10pm IST rollup email
│   ├── resume_otp_email.py   # 6-digit resume-login OTP email (no code in logs)
│   ├── log_config.py         # Stdout logging for Actions / local CLI
│   ├── dashboard.py          # Writes docs/index.html + data/jobs.json
│   ├── export_jobs.py        # SQLite snapshot for the Worker Jobs viewer
│   ├── mailer.py             # Shared Gmail SMTP
│   ├── db.py                 # SQLite helpers + migrations
│   └── config_loader.py      # config.yaml + repo paths
├── scripts/
│   ├── export_session.py     # Optional cookie export if password login blocked
│   └── commit_state.sh       # Commit DB + jobs.json + resume_versions.json + dashboard with conflict recovery
├── tests/
│   └── test_pipeline.py
├── data/
│   ├── jobs.db               # SQLite (committed after scan/submit)
│   ├── jobs.json             # Snapshot for /resumes/jobs (no approval_token)
│   ├── resume_versions.json  # Hash → resume text for receipts
│   └── resumes/
│       ├── fullstack.txt
│       ├── backend.txt
│       └── frontend.txt
├── docs/
│   └── index.html            # GitHub Pages status dashboard
├── config.yaml
├── requirements.txt
└── README.md
```

---

## 2. Data model (SQLite — `data/jobs.db`)

```sql
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,          -- sha256(company|role|url)[:32]
  company TEXT,
  role TEXT,
  url TEXT,
  jd_text TEXT,
  match_score REAL,
  resume_variant TEXT,          -- fullstack | backend | frontend
  draft_answer TEXT,
  status TEXT,                  -- discovered | drafted | pending_approval | approved | rejected | submitted | failed
  approval_token TEXT,
  discovered_at TEXT,
  decided_at TEXT,
  submitted_at TEXT,
  error_message TEXT,           -- truncated; cleared on successful retry
  sent_message TEXT,            -- exact apply note filled on submit (GitHub line included)
  match_breakdown TEXT,         -- JSON: filters, per-variant scores, top keywords
  resume_version_hash TEXT,     -- sha256 of winning resume text at match time
  drafted_at TEXT,
  confirmation_signal TEXT,     -- JSON after Send: {ok, url, text}
  github_run_id TEXT            -- GITHUB_RUN_ID when submit ran in Actions
);
CREATE TABLE resume_versions (
  hash TEXT PRIMARY KEY,
  variant TEXT,
  content TEXT,
  created_at TEXT
);
```

`db.py` creates the schema on connect and migrates missing job columns (`error_message`, `sent_message`, receipt fields) plus `resume_versions`.

**Status flow:** `discovered` → `drafted` → `pending_approval` → `submitted` / `failed` / `rejected`. Below-threshold jobs stay `discovered` and never hit email. A prior `failed` apply can be retried via Approve again.

SQLite-as-committed-file is fine at this scale. Scan / submit / report share concurrency group `jobs-db` (one writer, no cancel). `scripts/commit_state.sh` recovers from push conflicts by resetting to remote, restoring this run’s DB, regenerating the dashboard, and retrying with backoff.

---

## 3. Component details (as built)

### 3.1 Auth (`login.py` + `session.py`)
- Prefer `YC_EMAIL` / `YC_PASSWORD` against `account.ycombinator.com` (not magic-link email).
- Valid `YC_SESSION_COOKIES` tried first; `scripts/export_session.py` if password login is blocked (OAuth / 2FA).
- Login failure emails you; next scan (≤4h or manual) retries.

### 3.2 Discovery (`scrape.py` + `waas_parse.py`)
- After login, walks `search.sources` in `config.yaml` (default: remote eng, India eng, 1–2 years experience) with delays and page caps.
- Intercepts Algolia / `companies/fetch` JSON and Inertia `data-page`; inserts new rows as `discovered` (dedup by job URL / id hash).
- External/company-site apply listings are skipped later on submit and marked `failed` with `error_message`.

### 3.3 Matching (`match.py`)
- Hard filters (skip keywords, remote/India, role keywords) + TF-IDF cosine similarity + keyword overlap vs resume `.txt` variants.
- Writes `match_score`, best `resume_variant`, `match_breakdown` JSON (per-variant cosine/overlap, top overlapping terms, no hard-filter bonus), and `resume_version_hash` via `resume_versions`. Filtered jobs store breakdown with `match_score=0` and no hash. Below `match.threshold` never reaches digest.

### 3.4 Drafting (`draft.py` + `llm.py`)
- Only jobs above threshold. Gemini primary (`LLM_API_KEY`); OpenRouter free model fallback (`OPENROUTER_API_KEY`).
- Validates sentence count, no greeting/sign-off, first person, GitHub profile line; re-asks up to `draft.validation_retries`.
- Saves `draft_answer`, status `drafted`, `drafted_at`.

### 3.5 Approval email (`email_digest.py` + `tokens.py` + `mailer.py`)
- Bundles `drafted` jobs into one HTML email: company, role, score, draft, **Approve** / **Reject** links.
- Links use HMAC token (`job_id` + action + expiry) with `APPROVAL_HMAC_SECRET`.
- Sets status `pending_approval`. Sent via Gmail SMTP.

### 3.6 Approval handling (`worker/index.js` + `worker/approve.js`)
- `/` verifies HMAC + expiry, fires GitHub `repository_dispatch` (`job_approved` / `job_rejected`) with `job_id`.
- Worker secrets: `APPROVAL_HMAC_SECRET`, `GH_PAT_FOR_DISPATCH` (repo scope), `RECOVERY_EMAIL`. KV binding `RESUME_AUTH`. URL → `email.approval_base_url`.

### 3.6b Resume editor + jobs viewer (`worker/resumes.js` + `worker/jobs.js`)
- `/resumes` is a password-gated form. Seed name `dev` and seed password apply only when KV is empty. After a reset, name and password HMAC live in Cloudflare KV (`RESUME_AUTH`). Session cookie is HMAC of `resume-ui|{username}|{nonce}` with `APPROVAL_HMAC_SECRET` (`Path=/resumes`). Changing name or password bumps the nonce and logs everyone out.
- Forgot password / name (`worker/forgot.js` + `worker/auth.js`): enter `RECOVERY_EMAIL` → 6-digit OTP hashed in KV (10 min, 5 tries, 3 sends / 15 min) → GitHub `repository_dispatch` `resume_otp` → `resume-otp.yml` emails the code to `GMAIL_ADDRESS` only (30s–2min). Then set a new password or name. Wrong email still shows the code form (no leak). Keep the repo private; the OTP is in the Actions event payload until it expires.
- After login, loads and saves `data/resumes/fullstack.txt`, `backend.txt`, and `frontend.txt` via the GitHub Contents API (`GH_PAT_FOR_DISPATCH`).
- Next `scan.yml` checkout uses the new text. Already-scored / drafted jobs are not re-matched.
- `/resumes/jobs` (same cookie) is a read-only DB visualizer. List calls `/resumes/jobs.json?page=` (10 rows per page, `total_pages` in metadata). Page buttons show 10 at a time; Next/Prev and page numbers fetch the next slice from the Worker (the browser does not load the full snapshot). `/resumes/jobs?id=` loads one row (JD, draft, sent message, error, timestamps, score breakdown, hashed resume snapshot, timeline, Send confirmation). Joins `resume_version_hash` against `data/resume_versions.json`. No `approval_token`. Snapshot is the latest commit on `GH_BRANCH` (`worker/wrangler.toml`), loaded via Git blobs API with cache bypass. `data/jobs.json` is larger than 1MB, so the Contents API omits `content` and `raw.githubusercontent.com` would stay stale for hours.

### 3.7 Submission (`submit.yml` → `submit.py`)
- Approve only: Apply → fill LLM note (native input so Send enables) → Send. Note includes `github.profile_url`. The exact filled text is stored in `sent_message` (live Send, local dry-run, and failed apply). After Send, stores `confirmation_signal` (`ok` + page URL + body snippet) and `github_run_id`.
- Idempotency + `submit.daily_cap` (default 5). Reject path only marks rejected.
- Local: `python src/submit.py --job-id ID --dry-run` fills without Send. Actions force live Send after Approve (`SUBMIT_DRY_RUN=false`).

### 3.8 Notify + daily report + dashboard
- `notify.py` — email after approve/reject/submit (includes `error_message` on failure). Submit workflow writes a JSON payload in the `jobs-db` job, then the spectate job sends the mail so it can include the recording link without re-reading a stale checkout of `jobs.db`.
- `daily_report.py` + `report.yml` — ~10pm IST: previous 10pm→10pm window (survives post-midnight delay), applied/rejected/failed counts, rejected and failed job lists, errors grouped by message.
- `log_config.py` — stdout logging (`LOG_LEVEL`, default INFO) used by pipeline modules. `commit_state.sh` stays on `echo`.
- `dashboard.py` — regenerates `docs/index.html` for GitHub Pages and `data/jobs.json` + `data/resume_versions.json` for `/resumes/jobs` (scan + submit commit both).

### 3.9 Spectate (run recordings)
- `RECORD_RUN` defaults off. Scan/submit Actions set it `true`. Login/cookie check use an unrecorded context; scrape/submit use a second context with `record_video_dir`.
- Raw `.webm` clips stay on the runner and are uploaded as a 1-day artifact. A follow-on `spectate` job (not in `jobs-db`) merges with ffmpeg to H.264 mp4 (`spectate.crf` / `maxrate_k`), attaches it to a GitHub Release tagged `recording-<run_id>` (reruns: `recording-<run_id>-<attempt>`), and emails the Release page URL plus the Actions run URL.
- Prune deletes `recording-*` releases older than `spectate.retain_hours` (24) and any extras beyond `keep_releases`. It runs after each spectate job and hourly via `prune-recordings.yml` (no `jobs-db` lock). Email links 404 after delete.
- Releases are created with `GITHUB_TOKEN` (`contents: write`). They are not the Worker’s concern. The mp4 **downloads**; GitHub does not stream it inline. Keep the repo private. Videos are never committed.

---

## 4. GitHub Actions (current)

| Workflow | Trigger | Main steps |
|---|---|---|
| `scan.yml` | `0 */4 * * *` + `workflow_dispatch` | Job `scan` (`jobs-db`): cached Python + Playwright → init DB → scrape → match → draft → digest → dashboard → `commit_state.sh` → raw video artifact. Job `spectate` (no lock): merge → mp4 artifact → GitHub Release → prune → recap email. |
| `submit.yml` | `job_approved` / `job_rejected` | Job `handle` (`jobs-db`): reject **or** submit → notify payload → dashboard → commit → artifacts. Job `spectate`: merge → Release → prune → notify email with recording link. |
| `report.yml` | `30 16 * * *` UTC (~10pm IST) + manual | cached Python → `daily_report.py` (read-only on repo contents) |
| `resume-otp.yml` | `repository_dispatch` `resume_otp` | cached Python → `resume_otp_email.py` (Gmail to `GMAIL_ADDRESS` only; not in `jobs-db`) |
| `prune-recordings.yml` | `20 * * * *` + manual | cached Python → `prune_recordings.py` (no `jobs-db` lock) |

Scan / submit / report use concurrency group `jobs-db` with `cancel-in-progress: false` **on the DB-writing job only**. Spectate and resume-otp jobs are outside that group. Shared composite `.github/actions/setup-cached-python` restores `.venv` (and Playwright browsers for scan/submit) when `requirements.txt` + Python version match; otherwise it installs and saves the cache.

---

## 5. Secrets

### GitHub repo secrets

| Secret | Purpose |
|---|---|
| `YC_EMAIL` / `YC_PASSWORD` | Preferred auto-login |
| `YC_SESSION_COOKIES` | Optional cookie fallback |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | Digest, notify, scan recap, daily report, resume-login OTP |
| `LLM_API_KEY` | Gemini drafts |
| `OPENROUTER_API_KEY` | Free-tier fallback |
| `APPROVAL_HMAC_SECRET` | Sign/verify approval links (same as Worker) |

### Cloudflare Worker secrets (`wrangler secret put`)

| Secret | Purpose |
|---|---|
| `APPROVAL_HMAC_SECRET` | Verify email links and sign `/resumes` session cookie |
| `GH_PAT_FOR_DISPATCH` | Fire `repository_dispatch` (approve/reject + resume OTP), commit resume files from `/resumes`, read `data/jobs.json` |
| `RECOVERY_EMAIL` | Inbox that may request a forgot-password/name code (same as `GMAIL_ADDRESS`) |

Local: `.env` or `credentials.local.yaml` (never commit). See `.env.example` / `credentials.local.yaml.example`.

---

## 6. Dependencies (`requirements.txt`)

| Package | Use |
|---|---|
| `playwright` | Browser login / scrape / submit |
| `pyyaml` | `config.yaml` + local credentials |
| `scikit-learn` | TF-IDF matching |
| `httpx` | LLM HTTP |
| `python-dotenv` | `.env` loading |
| `tzdata` | `ZoneInfo("Asia/Kolkata")` on Windows / lean images |

Also: `python -m playwright install chromium` after pip.

---

## 7. Cost (unchanged intent)

| Component | Free tier | Expected use |
|---|---|---|
| GitHub Actions | 2,000 min/month (private) | Few min/run × scan every 4h + submits |
| Cloudflare Workers | 100k req/day | A few clicks/day |
| Gemini / OpenRouter free | Generous free quotas | Dozens of drafts/day |
| Gmail SMTP | Personal volume | Digest + notify + scan recap + 1 daily report + resume OTP |
| Cloudflare KV | 100k reads / 1k writes / day | Resume login name, password HMAC, OTP |
| GitHub Releases | Repo storage | `recording-*` mp4s, deleted after 24h |
| SQLite in repo | N/A | Free storage |

**Target: $0/month** at personal volume.

---

## 8. Build status

| Milestone | Status |
|---|---|
| Scrape + dedup + multi-source search | Done |
| SQLite + match + resume variants (`.txt`) | Done |
| Draft (Gemini + OpenRouter + validation) | Done |
| Email digest + HMAC tokens | Done |
| Cloudflare Worker + repository_dispatch | Done |
| Hosted `/resumes` editor (Worker → GitHub Contents API) | Done |
| Hosted `/resumes/jobs` DB visualizer (`data/jobs.json`) | Done |
| Submit with daily cap + dry-run locally | Done |
| Password login + cookie fallback | Done |
| Notify + daily IST report | Done |
| GitHub Pages dashboard + conflict-safe commit | Done |
| Pipeline tests (`tests/test_pipeline.py`) | Done |
| Spectate (record after login, mp4 artifact, GitHub Release, prune, email link) | Done |
| Resume login forgot password / name (OTP via Actions + KV) | Done |
| Application receipt (match breakdown, hashed resume versions, Send confirmation on `/resumes/jobs?id=`) | Done |

**Local smoke order:** `test_pipeline.py` → scrape → match → draft → digest → `submit.py --dry-run` → dashboard. Optional: `RECORD_RUN=true` on scrape/submit to write local `.webm` clips.

---

## 9. Key risks

- Automated submission on workatastartup.com may violate ToS; aggressive automation can flag accounts. Approval gate + daily cap stay non-negotiable.
- Site DOM / Algolia / login flows change; scrape and submit are the fragile edges.
- LLM free tiers rate-limit or delist models — keep OpenRouter model configurable in `config.yaml`.
- Concurrent Actions writing `jobs.db` — mitigated by `jobs-db` concurrency + `commit_state.sh`.
