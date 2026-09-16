# Wellfound Job Apply Automation — Current Plan

Human-gated pipeline for [Wellfound](https://wellfound.com) (formerly AngelList Talent): scrape → score → draft → email digest → Approve/Reject. **Live send is not implemented.** Approve records `approved` only.

YC Work at a Startup stays on its own plan: [`plan.md`](plan.md). Same repo, same Gmail inbox, same Cloudflare Worker, same Gemini / OpenRouter keys. Separate login, scrape, scan workflow, and dispatch events.

Zero-cost stack is unchanged: GitHub Actions + Cloudflare Workers + SQLite-in-repo + Gmail SMTP + Gemini free tier (OpenRouter `:free` fallback).

Automated access may conflict with Wellfound’s ToS. Keep the approval gate. Never fully autonomous submit.

---

## 1. Repo structure (Wellfound additions)

```
Yc-auto-apply/
├── wellfound-plan.md         # this file
├── plan.md                   # YC board (unchanged product)
├── .github/workflows/
│   ├── scan-wellfound.yml    # every 8h: login → scrape → match → draft → digest → dashboard → commit
│   └── submit-wellfound.yml  # repository_dispatch: reject mark / approve stub (no Playwright Send)
├── src/wellfound/
│   ├── login.py              # WELLFOUND_EMAIL / password (cookies fallback)
│   ├── session.py            # Playwright context; record after login
│   ├── parse.py              # Listing JSON / __NEXT_DATA__ → company/role/url/jd
│   ├── scrape.py             # Walk wellfound.search.sources; intercept XHR/GraphQL
│   └── submit.py             # Stub: status approved, no live Send
├── src/
│   ├── db.py                 # jobs.source (yc | wellfound)
│   ├── tokens.py             # HMAC; wellfound signs jobId|action|expiry|wellfound
│   ├── match.py              # --source (default yc)
│   ├── draft.py              # --source (default yc)
│   └── email_digest.py       # --source (default yc); wellfound digest subject
├── worker/approve.js         # yc → job_approved; wellfound → wellfound_job_approved
└── config.yaml               # wellfound: login, search, digest subject, submit.enabled
```

YC modules (`src/login.py`, `src/scrape.py`, `src/submit.py`, `scan.yml`, `submit.yml`) are not the Wellfound adapters.

---

## 2. Data model

Same `data/jobs.db` as YC, plus:

```sql
source TEXT  -- yc | wellfound; existing rows backfilled to yc
```

Job id is still `sha256(company|role|url)[:32]`. Different listing URLs → different rows if the same company is on both boards.

**Status flow (this ship):** `discovered` → `drafted` → `pending_approval` → `approved` / `rejected`. Below-threshold jobs stay `discovered`. Live `submitted` is a later milestone.

Scan / submit-wellfound share concurrency group `jobs-db` with YC writers (one SQLite writer, no cancel).

---

## 3. Component details

### 3.1 Auth (`src/wellfound/login.py` + `session.py`)

- Prefer `WELLFOUND_EMAIL` / `WELLFOUND_PASSWORD` against wellfound.com login.
- Valid `WELLFOUND_SESSION_COOKIES` tried first (Playwright cookie JSON, same encoding as YC).
- Login failure emails you; next scan (≤8h or manual) retries.
- Do not use `YC_*` secrets.

### 3.2 Discovery (`scrape.py` + `parse.py`)

- After login, walks `wellfound.search.sources` in `config.yaml` (default: remote eng, India eng) with delays and page caps.
- Intercepts GraphQL / jobs JSON and `__NEXT_DATA__`; inserts new rows as `discovered` with `source=wellfound`.
- Company-site / ATS-only listings are skipped when the apply URL is off wellfound.com.

### 3.3 Matching / drafting / digest

- Same TF-IDF match, resume `.txt` variants, Gemini + OpenRouter drafts as YC.
- CLI `--source wellfound` so YC scan cannot draft or email Wellfound rows (and the reverse).
- Digest subject from `wellfound.email.digest_subject`. Approve/Reject links include signed `source=wellfound`.
- Same Worker URL (`email.approval_base_url`).

### 3.4 Approval handling

- Worker verifies HMAC. Missing/`yc` source keeps `jobId|action|expiry` and fires `job_approved` / `job_rejected` (existing YC emails).
- `source=wellfound` is in the signature (`jobId|action|expiry|wellfound`) and fires `wellfound_job_approved` / `wellfound_job_rejected`.
- Flipping `source` on a YC link fails verify.

### 3.5 Submission (stub)

- Reject: `python src/db.py --mark-rejected`.
- Approve: `src/wellfound/submit.py` sets `approved` and stores `error_message` that live Send is not implemented. No Playwright. Notify still emails.
- `wellfound.submit.enabled: false` until Easy Apply ships.

### 3.6 Shared product layer

Resumes editor, jobs viewer (`source` column + filter), dashboard, Gmail, spectate on **scan-wellfound** (not on the submit stub), daily report (YC window; Wellfound rows appear once they exist).

**Deploy the Worker** after `approve.js` changes (`npx wrangler deploy` in `worker/`). Until then Wellfound Approve links will 400 or hit the wrong event.

---

## 4. GitHub Actions

| Workflow | Trigger | Main steps |
|---|---|---|
| `scan-wellfound.yml` | `0 */8 * * *` + `workflow_dispatch` | Job `scan` (`jobs-db`): cached Python + Playwright → init DB → wellfound scrape → `match.py --source wellfound` → `draft.py --source wellfound` → `email_digest.py --source wellfound` → dashboard → `commit_state.sh` → raw video. Job `spectate` (no lock): merge → Release → prune → recap email. |
| `submit-wellfound.yml` | `wellfound_job_approved` / `wellfound_job_rejected` | Job `handle` (`jobs-db`): reject **or** approve stub → notify payload → dashboard → commit. No spectate. |

YC `scan.yml` / `submit.yml` event types are unchanged.

---

## 5. Secrets

### Shared (already set for YC)

| Secret | Purpose |
|---|---|
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | Wellfound digest, notify, scan recap |
| `LLM_API_KEY` / `OPENROUTER_API_KEY` | Drafts |
| `APPROVAL_HMAC_SECRET` | Same as Worker; YC and Wellfound links |

Worker secrets unchanged: `APPROVAL_HMAC_SECRET`, `GH_PAT_FOR_DISPATCH`, `RECOVERY_EMAIL`.

### Wellfound-only (GitHub repo secrets)

| Secret | Purpose |
|---|---|
| `WELLFOUND_EMAIL` / `WELLFOUND_PASSWORD` | Preferred auto-login |
| `WELLFOUND_SESSION_COOKIES` | Optional cookie fallback |

Local: `.env` or `credentials.local.yaml` keys `wellfound_email` / `wellfound_password` (never commit).

---

## 6. Cost

Same free-tier intent as `plan.md`. Extra Actions minutes: Wellfound scan every 8h (Playwright) plus rare stub submits. Target remains **$0/month** at personal volume.

---

## 7. Build status

| Milestone | Status |
|---|---|
| `source` column + YC backfill | Done |
| Source-signed HMAC + Worker dispatch split | Done |
| Wellfound login + scrape + parse | Done |
| Match / draft / digest `--source wellfound` | Done |
| `scan-wellfound.yml` (8h + spectate) | Done |
| `submit-wellfound.yml` approve stub (no live Send) | Done |
| Live Easy Apply / PDF / screening questions | Not started |

**Local smoke:** `python tests/test_pipeline.py` → `python src/wellfound/scrape.py` → `python src/match.py --source wellfound` → `python src/draft.py --source wellfound` → `python src/email_digest.py --source wellfound`.

---

## 8. Key risks

- Automated access on wellfound.com may violate ToS; CAPTCHA / Google login is more common than YC. Cookie fallback matters.
- Listing JSON and apply UI change often; scrape is the fragile edge.
- Volume is higher than WAAS — keep page caps low; do not fold Wellfound into the 4-hour YC scan.
- Concurrent Actions writing `jobs.db` — same `jobs-db` group + `commit_state.sh`.
- Worker must be redeployed or Wellfound Approve is unsafe / broken.
