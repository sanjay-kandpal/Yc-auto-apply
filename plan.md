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
│   └── workflows/
│       ├── scan.yml          # every 4h: login → scrape → match → draft → digest → dashboard → commit
│       ├── submit.yml        # repository_dispatch: approve submit / reject mark + notify
│       └── report.yml        # ~10pm IST: daily report email
├── worker/
│   ├── index.js              # Router: / approve, /resumes editor
│   ├── approve.js            # HMAC verify → GitHub repository_dispatch
│   ├── resumes.js            # Login + editor; commits data/resumes/*.txt
│   └── wrangler.toml
├── src/
│   ├── login.py              # YC email/password login (cookies fallback)
│   ├── session.py            # Playwright browser/context lifecycle
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
│   ├── dashboard.py          # Writes docs/index.html
│   ├── mailer.py             # Shared Gmail SMTP
│   ├── db.py                 # SQLite helpers + migrations
│   └── config_loader.py      # config.yaml + repo paths
├── scripts/
│   ├── export_session.py     # Optional cookie export if password login blocked
│   └── commit_state.sh       # Commit DB + dashboard with conflict recovery
├── tests/
│   └── test_pipeline.py
├── data/
│   ├── jobs.db               # SQLite (committed after scan/submit)
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
  error_message TEXT            -- truncated; cleared on successful retry
);
```

`db.py` creates the schema on connect and migrates `error_message` if missing.

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
- Writes `match_score` and best `resume_variant`. Below `match.threshold` never reaches digest.

### 3.4 Drafting (`draft.py` + `llm.py`)
- Only jobs above threshold. Gemini primary (`LLM_API_KEY`); OpenRouter free model fallback (`OPENROUTER_API_KEY`).
- Validates sentence count, no greeting/sign-off, first person, GitHub profile line; re-asks up to `draft.validation_retries`.
- Saves `draft_answer`, status `drafted`.

### 3.5 Approval email (`email_digest.py` + `tokens.py` + `mailer.py`)
- Bundles `drafted` jobs into one HTML email: company, role, score, draft, **Approve** / **Reject** links.
- Links use HMAC token (`job_id` + action + expiry) with `APPROVAL_HMAC_SECRET`.
- Sets status `pending_approval`. Sent via Gmail SMTP.

### 3.6 Approval handling (`worker/index.js` + `worker/approve.js`)
- `/` verifies HMAC + expiry, fires GitHub `repository_dispatch` (`job_approved` / `job_rejected`) with `job_id`.
- Worker secrets: `APPROVAL_HMAC_SECRET`, `GH_PAT_FOR_DISPATCH` (repo scope). URL → `email.approval_base_url`.

### 3.6b Resume editor (`worker/resumes.js`)
- `/resumes` is a password-gated form (hardcoded name `dev` in `worker/resumes.js`). Session cookie is HMAC-signed with `APPROVAL_HMAC_SECRET`.
- After login, loads and saves `data/resumes/fullstack.txt`, `backend.txt`, and `frontend.txt` via the GitHub Contents API (`GH_PAT_FOR_DISPATCH`).
- Next `scan.yml` checkout uses the new text. Already-scored / drafted jobs are not re-matched.

### 3.7 Submission (`submit.yml` → `submit.py`)
- Approve only: Apply → fill LLM note (native input so Send enables) → Send. Note includes `github.profile_url`.
- Idempotency + `submit.daily_cap` (default 5). Reject path only marks rejected.
- Local: `python src/submit.py --job-id ID --dry-run` fills without Send. Actions force live Send after Approve (`SUBMIT_DRY_RUN=false`).

### 3.8 Notify + daily report + dashboard
- `notify.py` — email after approve/reject/submit (includes `error_message` on failure).
- `daily_report.py` + `report.yml` — ~10pm IST: previous 10pm→10pm window (survives post-midnight delay), applied/failed counts, failed jobs, errors grouped by message.
- `dashboard.py` — regenerates `docs/index.html` for GitHub Pages (scan + submit commit it).

---

## 4. GitHub Actions (current)

| Workflow | Trigger | Main steps |
|---|---|---|
| `scan.yml` | `0 */4 * * *` + `workflow_dispatch` | cached Python + Playwright → init DB → scrape → match → draft → digest → dashboard → `commit_state.sh` |
| `submit.yml` | `job_approved` / `job_rejected` | cached Python + Playwright → reject mark **or** submit → notify → dashboard → commit |
| `report.yml` | `30 16 * * *` UTC (~10pm IST) + manual | cached Python → `daily_report.py` (read-only on repo contents) |

All three use concurrency group `jobs-db` with `cancel-in-progress: false`. Shared composite `.github/actions/setup-cached-python` restores `.venv` (and Playwright browsers for scan/submit) when `requirements.txt` + Python version match; otherwise it installs and saves the cache.

---

## 5. Secrets

### GitHub repo secrets

| Secret | Purpose |
|---|---|
| `YC_EMAIL` / `YC_PASSWORD` | Preferred auto-login |
| `YC_SESSION_COOKIES` | Optional cookie fallback |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | Digest, notify, daily report |
| `LLM_API_KEY` | Gemini drafts |
| `OPENROUTER_API_KEY` | Free-tier fallback |
| `APPROVAL_HMAC_SECRET` | Sign/verify approval links (same as Worker) |

### Cloudflare Worker secrets (`wrangler secret put`)

| Secret | Purpose |
|---|---|
| `APPROVAL_HMAC_SECRET` | Verify email links and sign `/resumes` session cookie |
| `GH_PAT_FOR_DISPATCH` | Fire `repository_dispatch` and commit resume files from `/resumes` |

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
| Gmail SMTP | Personal volume | Digest + notify + 1 daily report |
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
| Submit with daily cap + dry-run locally | Done |
| Password login + cookie fallback | Done |
| Notify + daily IST report | Done |
| GitHub Pages dashboard + conflict-safe commit | Done |
| Pipeline tests (`tests/test_pipeline.py`) | Done |

**Local smoke order:** `test_pipeline.py` → scrape → match → draft → digest → `submit.py --dry-run` → dashboard.

---

## 9. Key risks

- Automated submission on workatastartup.com may violate ToS; aggressive automation can flag accounts. Approval gate + daily cap stay non-negotiable.
- Site DOM / Algolia / login flows change; scrape and submit are the fragile edges.
- LLM free tiers rate-limit or delist models — keep OpenRouter model configurable in `config.yaml`.
- Concurrent Actions writing `jobs.db` — mitigated by `jobs-db` concurrency + `commit_state.sh`.
