# YC Job Apply Automation — Implementation Plan

Zero-cost stack: GitHub Actions + Cloudflare Workers (free tier) + SQLite-in-repo + Gmail SMTP + Groq/Gemini free LLM tier.

---

## 1. Repo structure

```
yc-job-bot/
├── .github/
│   └── workflows/
│       ├── scan.yml            # cron: discover + draft + email digest
│       └── submit.yml          # triggered by repository_dispatch on approval
├── worker/
│   └── approve.js              # Cloudflare Worker: handles approve-link clicks
├── src/
│   ├── scrape.py                # Playwright: pull job listings
│   ├── match.py                  # score jobs against resume
│   ├── draft.py                  # LLM call: tailored answer per job
│   ├── email_digest.py           # build + send daily digest email
│   ├── submit.py                  # Playwright: log in, fill form, submit
│   ├── notify.py                  # confirmation email
│   └── db.py                      # SQLite helpers
├── data/
│   ├── jobs.db                     # SQLite (committed after each run)
│   └── resumes/
│       ├── fullstack.pdf
│       ├── backend.pdf
│       └── ml.pdf
├── config.yaml                      # your filters, thresholds, rate limits
└── requirements.txt
```

---

## 2. Data model (SQLite — `data/jobs.db`)

```sql
CREATE TABLE jobs (
  id TEXT PRIMARY KEY,          -- hash of company+role+url
  company TEXT,
  role TEXT,
  url TEXT,
  jd_text TEXT,
  match_score REAL,
  resume_variant TEXT,
  draft_answer TEXT,
  status TEXT,                  -- discovered | drafted | pending_approval | approved | rejected | submitted | failed
  approval_token TEXT,
  discovered_at TEXT,
  decided_at TEXT,
  submitted_at TEXT
);
```

SQLite as a committed file works fine at this scale (tens of jobs/day) and needs zero external infra. If it ever gets awkward with Git conflicts, swap in Supabase's free Postgres tier — same schema.

---

## 3. Component details

### 3.1 Discovery (`scrape.py`)
- Playwright (headless Chromium) navigates the "Work at a Startup" jobs board using your logged-in session (cookies stored as a GitHub Actions secret, base64-encoded).
- Pull: company, role, JD text, listing URL, posted date.
- Insert new rows into `jobs` with `status = discovered`. Skip anything whose `id` hash already exists (dedup).
- **Rate limit yourself**: random delay (2–6s) between page loads, cap total pages per run (e.g. 20), run at most twice a day. This matters more than anything else for not tripping anti-bot detection.

### 3.2 Matching (`match.py`)
- Simple, cheap approach that doesn't need an LLM call per job: TF-IDF or keyword overlap between your resume text and the JD, plus hard filters (role type, remote/India-friendly, seniority keywords).
- Output a 0–100 `match_score`. Anything below your threshold (`config.yaml`, e.g. 70) gets logged but never reaches an email — keeps the digest short.
- Pick the best-matching resume variant (`fullstack` / `backend` / `ml`) by keyword overlap with each variant.

### 3.3 Drafting (`draft.py`)
- Only runs for jobs above threshold.
- One LLM call per job: prompt = your base resume bullet points + the JD + a short instruction to write the "why this company / why this role" answer in your voice, 3–5 sentences.
- Use Groq (Llama 3.1/3.3, generous free tier, very fast) or Gemini free tier for $0 token cost. Store the API key as a GitHub secret.
- Save result to `draft_answer`, set `status = drafted`.

### 3.4 Approval email (`email_digest.py`)
- One run at the end of `scan.yml`: bundle all `drafted` jobs into a single HTML email — company, role, match score, the drafted answer, and two links per job: **Approve** and **Reject**.
- Each link encodes a signed token: `HMAC(job_id + action + expiry, secret)` as a query param — this is what lets the Worker trust the click without a login system.
- Send via Gmail SMTP (app password stored as a secret) — free, no third-party email service needed.

### 3.5 Approval handling (`worker/approve.js`, Cloudflare Worker)
- Public HTTPS endpoint the email links point to.
- On request: verify the HMAC signature and expiry, then call the GitHub REST API to fire a `repository_dispatch` event on your repo with `event_type: job_approved` (or `job_rejected`) and the `job_id` as payload.
- GitHub Personal Access Token (repo scope only) stored as a Worker secret.
- Respond with a plain "✅ Approved — application will be submitted shortly" HTML page.
- Cloudflare Workers free tier gives 100,000 requests/day — vastly more than you'll ever need.

### 3.6 Submission (`submit.yml` → `submit.py`)
- Triggered only by the `repository_dispatch` event above (not on a schedule).
- Reads the `job_id` from the event payload, loads the row, checks `status == pending_approval` (idempotency guard against double-clicks).
- Playwright: restore session cookies, navigate to the job's apply page, fill in the drafted answer + resume upload, submit.
- On success: `status = submitted`, `submitted_at = now`. On failure: `status = failed`, log the error.
- **Daily cap**: check how many jobs already have `status = submitted` today before proceeding; hard-stop past your configured limit (e.g. 5/day) regardless of how many approvals came in.

### 3.7 Confirmation (`notify.py`)
- After `submit.py` finishes (success or failure), send a short email: "Applied to {role} at {company}" or "Failed to submit — needs manual follow-up," with a link to the listing.
- Commit the updated `jobs.db` back to the repo as the last step of the workflow (`git add data/jobs.db && git commit && git push` using the built-in `GITHUB_TOKEN`).

---

## 4. GitHub Actions workflows (sketch)

**`scan.yml`**
```yaml
on:
  schedule:
    - cron: "0 4,14 * * *"   # twice daily
  workflow_dispatch: {}
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt && playwright install chromium
      - run: python src/scrape.py
      - run: python src/match.py
      - run: python src/draft.py
      - run: python src/email_digest.py
      - run: git config user.email "bot@you" && git add data/jobs.db && git commit -m "scan run" || true
      - run: git push
```

**`submit.yml`**
```yaml
on:
  repository_dispatch:
    types: [job_approved, job_rejected]
jobs:
  handle:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt && playwright install chromium
      - if: github.event.action == 'job_approved'
        run: python src/submit.py --job-id "${{ github.event.client_payload.job_id }}"
      - if: github.event.action == 'job_rejected'
        run: python src/db.py --mark-rejected "${{ github.event.client_payload.job_id }}"
      - run: python src/notify.py --job-id "${{ github.event.client_payload.job_id }}"
      - run: git config user.email "bot@you" && git add data/jobs.db && git commit -m "submit run" || true
      - run: git push
```

---

## 5. Secrets to configure (GitHub repo → Settings → Secrets)

| Secret | Purpose |
|---|---|
| `YC_SESSION_COOKIES` | base64-encoded logged-in session for workatastartup.com |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | sending digest + confirmation emails |
| `LLM_API_KEY` | Groq or Gemini free-tier key for drafting |
| `APPROVAL_HMAC_SECRET` | signs/verifies approval links |
| `GH_PAT_FOR_DISPATCH` | used by the Cloudflare Worker to call `repository_dispatch` (repo scope only) |

Cloudflare Worker secrets are set separately via `wrangler secret put`.

---

## 6. Cost breakdown

| Component | Free tier limit | Your expected usage |
|---|---|---|
| GitHub Actions (private repo) | 2,000 min/month | Well under, a few min/run × 2/day |
| Cloudflare Workers | 100k requests/day | A handful of clicks/day |
| Groq/Gemini free tier | Thousands of tokens/day free | A few dozen drafts/day |
| Gmail SMTP | No hard cap for personal volume | 1–2 emails/day |
| SQLite in repo | N/A | Free, just repo storage |

**Total: $0/month** at this usage level.

---

## 7. Build order (suggested milestones)

1. `scrape.py` — get job discovery + dedup working, print to console first, no DB yet.
2. Add SQLite + `match.py` — verify scoring makes sense on real listings before automating further.
3. `draft.py` — wire up the free LLM tier, sanity-check the tone of generated answers manually.
4. `email_digest.py` — get the digest email looking right (this is what you'll see every day).
5. Cloudflare Worker + HMAC tokens — the trickiest plumbing; test with a manual token first.
6. `submit.py` — build and test against a throwaway/test application before trusting it on real ones.
7. Wire the two GitHub Actions workflows together, add the daily cap + idempotency checks.
8. Add the GitHub Pages status dashboard once the core loop is stable.

---

## 8. Key risk to keep in mind

Automated submission on workatastartup.com likely isn't sanctioned by their ToS, and aggressive automation can get an account flagged. The design here keeps every submission gated behind your explicit approval and caps daily volume — treat that gate as non-negotiable even as you iterate, rather than ever moving to a fully autonomous auto-submit.