# Wellfound Job Apply Automation — Current Plan

Human-gated pipeline for [Wellfound](https://wellfound.com) (formerly AngelList Talent): scrape → score → draft → email digest → Approve runs an **Apply form probe** (classify only). **Live Send is not implemented.**

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
│   ├── scan-wellfound.yml    # 02:00/10:00/18:00 UTC (8h, 2h off YC): scrape → match → draft → digest → commit
│   └── submit-wellfound.yml  # repository_dispatch: reject / Approve form probe (+ spectate)
├── src/wellfound/
│   ├── login.py              # WELLFOUND_EMAIL / password (cookies fallback)
│   ├── session.py            # Playwright context; record after login
│   ├── parse.py              # Listing JSON / __NEXT_DATA__ → company/role/url/jd
│   ├── filters.py            # /jobs Filters popup: roles, full-time, 0–3y, View results
│   ├── scrape.py             # Login → /jobs → filters UI → intercept XHR/GraphQL
│   ├── apply_probe.py        # Open Apply UI → classify apply_kind (never Send)
│   └── submit.py             # Probe on Approve; refuses live Send
├── src/
│   ├── db.py                 # jobs.source + apply_kind; submitted_today(source=)
│   ├── tokens.py             # HMAC; wellfound signs jobId|action|expiry|wellfound
│   ├── match.py              # --source; wellfound.filters.eligibility_skip_keywords
│   ├── draft.py              # --source (default yc)
│   └── email_digest.py       # --source; Wellfound Approve = form probe wording
├── worker/approve.js         # yc → job_approved; wellfound → wellfound_job_approved
└── config.yaml               # wellfound: login, search, filters, digest, submit.enabled:false
```

YC modules (`src/login.py`, `src/scrape.py`, `src/submit.py`, `scan.yml`, `submit.yml`) are not the Wellfound adapters.

---

## 2. Data model

Same `data/jobs.db` as YC, plus:

```sql
source TEXT       -- yc | wellfound; existing rows backfilled to yc
apply_kind TEXT   -- cover_letter_only | has_questions | eligibility_blocked | external_ats | unknown | null
```

Job id is still `sha256(company|role|url)[:32]`. Different listing URLs → different rows if the same company is on both boards.

**Funnel:** scrape → hard filters + score → draft → digest → Approve **probes** form:
- `cover_letter_only` → status `approved` (human OK + form looks bot-safe); `sent_message` may hold the intended note; **no Send**
- `has_questions` / `eligibility_blocked` / `external_ats` / `unknown` → status `failed` + reason in `error_message`

Live `submitted` remains a later milestone. `submitted_today(conn, source="wellfound")` is wired for the future cap; `wellfound.submit.enabled` stays `false`.

Scan / submit-wellfound share concurrency group `jobs-db` with YC writers (one SQLite writer, no cancel).

---

## 3. Component details

### 3.1 Auth (`src/wellfound/login.py` + `session.py`)

- Prefer `WELLFOUND_EMAIL` / `WELLFOUND_PASSWORD` against wellfound.com login.
- Valid `WELLFOUND_SESSION_COOKIES` tried first (Playwright cookie JSON, same encoding as YC).
- Login failure emails you; next scan (≤8h or manual) retries.
- Do not use `YC_*` secrets.

### 3.2 Discovery (`filters.py` + `scrape.py` + `parse.py`)

- After login, opens `wellfound.search.jobs_url` (`https://wellfound.com/jobs`), clicks **Filters**, applies config-driven roles (Software Engineer + Full-Stack Engineer), **Full Time**, experience **0–3 years** (include listings with no experience), then **View results**.
- Does **not** navigate to pre-baked `/role/...` URLs.
- Network JSON is collected by enqueueing interesting responses (graphql / job search / algolia only) and reading bodies on the main thread — never inside the sync `page.on("response")` handler (that deadlocks Playwright).
- Intercepts job JSON and `__NEXT_DATA__`; inserts new rows as `discovered` with `source=wellfound`.
- Company-site / ATS-only listings are skipped when the apply URL is off wellfound.com.

### 3.3 Matching / drafting / digest

- Same TF-IDF match, resume `.txt` variants, Gemini + OpenRouter drafts as YC.
- Wellfound-only `wellfound.filters.eligibility_skip_keywords` (citizenship / US-only / must-be-based) hard-filter in `match.py` when `--source wellfound`.
- CLI `--source wellfound` so YC scan cannot draft or email Wellfound rows (and the reverse).
- Digest subject from `wellfound.email.digest_subject`. Approve/Reject links include signed `source=wellfound`.
- Digest copy: Approve runs a **form probe** only (not live Send).
- Same Worker URL (`email.approval_base_url`).

### 3.4 Approval handling

- Worker verifies HMAC. Missing/`yc` source keeps `jobId|action|expiry` and fires `job_approved` / `job_rejected` (existing YC emails).
- `source=wellfound` is in the signature (`jobId|action|expiry|wellfound`) and fires `wellfound_job_approved` / `wellfound_job_rejected`.
- Flipping `source` on a YC link fails verify.

### 3.5 Form probe (no Send)

- Reject: `python src/db.py --mark-rejected`.
- Approve: Playwright login → open listing → open Apply → classify via `apply_probe.py` → write `apply_kind` + status. **Never clicks Send.** Even if `wellfound.submit.enabled` is flipped early, this stage refuses Send.
- v1 bot-safe rule: only `cover_letter_only` stays `approved`; questions / blockers / ATS → `failed`.
- Notify emails include `apply_kind`. Spectate records the probe run (same pattern as YC submit).

### 3.6 Shared product layer

Resumes editor, jobs viewer (`source` + `apply_kind` column/filter), dashboard, Gmail, spectate on **scan-wellfound** and **submit-wellfound**, daily report (YC window; Wellfound rows appear once they exist).

**Deploy the Worker** after `approve.js` / jobs UI changes (`npx wrangler deploy` in `worker/`). Until then Wellfound Approve links will 400 or hit the wrong event.

---

## 4. Workflows

| Workflow | Trigger | What it does |
|---|---|---|
| `scan-wellfound.yml` | `0 2,10,18 * * *` UTC + `workflow_dispatch` | Job `scan` (`jobs-db`): cached Python + Playwright → init DB → wellfound scrape → `match.py --source wellfound` → `draft.py --source wellfound` → `email_digest.py --source wellfound` → dashboard → `commit_state.sh` → raw video. Job `spectate` (no lock): merge → Release → prune → recap email. Starts 2h after a YC 4h scan so the two boards never share a start hour (YC is `0 */4 * * *`). |
| `submit-wellfound.yml` | `wellfound_job_approved` / `wellfound_job_rejected` | Reject marks rejected. Approve runs form probe with Playwright + WELLFOUND_* + optional spectate. No live Send. |

---

## 5. Secrets

| Secret | Role |
|---|---|
| Shared with YC | `GMAIL_*`, `APPROVAL_HMAC_SECRET`, `LLM_API_KEY` / `OPENROUTER_API_KEY` |
| `WELLFOUND_EMAIL` / `WELLFOUND_PASSWORD` | Preferred auto-login |
| `WELLFOUND_SESSION_COOKIES` | Optional cookie fallback |

Local: `.env` or `credentials.local.yaml` keys `wellfound_email` / `wellfound_password` (never commit).

---

## 6. Cost

Same free-tier intent as `plan.md`. Extra Actions minutes: Wellfound scan every 8h (Playwright) plus rare probe submits. Target remains **$0/month** at personal volume.

---

## 7. Build status

| Milestone | Status |
|---|---|
| `source` column + YC backfill | Done |
| Source-signed HMAC + Worker dispatch split | Done |
| Wellfound login + scrape + parse | Done |
| `/jobs` Filters UI (roles, full-time, 0–3y, View results) | Done |
| Match / draft / digest `--source wellfound` | Done |
| Wellfound eligibility skip keywords | Done |
| `apply_kind` + form probe on Approve | Done |
| `scan-wellfound.yml` (8h + spectate) | Done |
| `submit-wellfound.yml` probe (+ spectate) | Done |
| Live Send / multi-step Apply clicks | Not started — blocked on DOM procedure |

**Local smoke:** `python tests/test_pipeline.py` → `python tests/test_apply_probe.py` → `python src/wellfound/scrape.py` → `python src/match.py --source wellfound` → `python src/draft.py --source wellfound` → `python src/email_digest.py --source wellfound` → (optional) `python src/wellfound/submit.py --job-id ID`.

---

## 8. Key risks

- Automated access on wellfound.com may violate ToS; CAPTCHA / Google login is more common than YC. Cookie fallback matters.
- Listing JSON and apply UI change often; scrape/probe selectors are the fragile edge.
- Volume is higher than WAAS — keep page caps low; do not fold Wellfound into the 4-hour YC scan.
- Concurrent Actions writing `jobs.db` — same `jobs-db` group + `commit_state.sh`.
- Worker must be redeployed or Wellfound Approve is unsafe / broken.
