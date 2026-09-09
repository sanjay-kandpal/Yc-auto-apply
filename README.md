# YC auto-apply

Human-gated job pipeline for [Work at a Startup](https://www.workatastartup.com): scrape listings, score them against your resume, draft an answer, email you a digest, and submit **only after you click Approve**.

Zero-cost stack: GitHub Actions + Cloudflare Worker + SQLite in this repo + Gmail SMTP + Gemini free tier (OpenRouter `:free` fallback).

Automated apply is likely against the site’s terms. Keep the approval gate and daily cap on. Never turn this into fully autonomous submit.

**Deep product guide (architecture, every module, incidents, backlog):** [docs/YC-auto-apply-product-guide.docx](docs/YC-auto-apply-product-guide.docx)

**Production hardening and free path toward ~100 users:** [docs/YC-auto-apply-production-scale.docx](docs/YC-auto-apply-production-scale.docx)

## What exists

| Path | Role |
|---|---|
| `src/login.py` | Playwright email/password login. On failure, emails you to update credentials; next 4-hour run (or manual scan) retries. |
| `src/scrape.py` | Playwright after login. Walks `search.sources` (remote, India, 1–2 years). Intercepts Algolia / `companies/fetch` JSON and Inertia `data-page`, then stores new rows (dedup by URL). |
| `src/match.py` | TF-IDF + keyword overlap + hard filters. Writes `match_score` and `resume_variant`. |
| `src/draft.py` | One Gemini call per job above threshold; OpenRouter `:free` if Gemini fails. Caps at `draft.requests_per_minute` (default 5); waits 60s and retries once on 429. |
| `src/email_digest.py` | HTML digest with HMAC Approve/Reject links. Sets `pending_approval`. |
| `worker/approve.js` | Verifies the link, fires GitHub `repository_dispatch`. |
| `src/submit.py` | After Approve only: click Apply → fill LLM note → Send. Daily cap. Local `--dry-run` skips Send. |
| `src/notify.py` | Confirmation email after approve/reject/submit (includes stored error on failure). |
| `src/daily_report.py` | 10pm IST daily email: applied/failed counts, failed jobs, errors grouped by identical message. |
| `src/dashboard.py` | Writes `docs/index.html` for GitHub Pages. |
| `src/db.py` | SQLite helpers. Migrates `error_message` on connect. `python src/db.py --init` / `--mark-rejected ID`. |
| `scripts/export_session.py` | Optional cookie fallback if password login is blocked (OAuth / 2FA). |
| `config.yaml` | Filters, threshold, delays, cap, LLM provider, Worker URL. |
| `.github/workflows/scan.yml` | Every 4 hours (`0 */4 * * *`) plus manual Run workflow. |
| `.github/workflows/submit.yml` | Runs on `job_approved` / `job_rejected`. |
| `.github/workflows/report.yml` | ~10pm IST (`30 16 * * *` UTC) plus manual Run workflow — emails the daily report. |

Status flow: `discovered` → `drafted` → `pending_approval` → `submitted` / `failed` / `rejected`. Below-threshold jobs stay `discovered` and never hit email.

## One-time setup

1. Python 3.12+: `pip install -r requirements.txt && python -m playwright install chromium`
2. Copy `.env.example` to `.env` **or** `credentials.local.yaml.example` to `credentials.local.yaml` and put your Work at a Startup email/password there. Never commit those files.
3. Replace `data/resumes/*.txt` with your real bullets (matching + LLM voice).
4. Fill `data/resumes/*.txt` (matching, drafts, and the apply message). PDFs are not required.
5. Edit `config.yaml`: `search.sources` URLs (copy from the jobs board after you set filters), `email.approval_base_url`, `github.owner` / `github.repo`.
6. Edit `worker/wrangler.toml` `[vars]` `GH_OWNER` / `GH_REPO` to match.
7. `python src/db.py --init` (already done in a fresh clone if `data/jobs.db` exists).
8. Set `YC_EMAIL` and `YC_PASSWORD` (preferred). Cookie export is only a fallback.

### GitHub secrets (repo → Settings → Secrets)

| Secret | Purpose |
|---|---|
| `YC_EMAIL` / `YC_PASSWORD` | auto-login on every scan/submit |
| `YC_SESSION_COOKIES` | optional cookie fallback |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | digest + confirmation + daily report mail |
| `LLM_API_KEY` | Gemini (primary drafts) |
| `OPENROUTER_API_KEY` | OpenRouter free-tier fallback (`google/gemma-4-31b-it:free`) |
| `APPROVAL_HMAC_SECRET` | signs/verifies approval links (same value as the Worker secret) |
| `SUBMIT_DRY_RUN` | unused on GitHub now (`submit.yml` forces live Send after Approve). Use `--dry-run` locally only. |

### Cloudflare Worker

```bash
cd worker
npx wrangler login
npx wrangler secret put APPROVAL_HMAC_SECRET
npx wrangler secret put GH_PAT_FOR_DISPATCH
npx wrangler deploy
```

The PAT needs `repo` scope so it can send `repository_dispatch`. Put the Worker URL into `email.approval_base_url`.

### GitHub Pages

After the first scan commits `docs/index.html`, enable Pages on the `docs/` folder.

## Local dry-run order

```bash
python tests/test_pipeline.py
python src/tokens.py
python src/scrape.py
python src/match.py
python src/draft.py
python src/email_digest.py
python src/submit.py --job-id ID --dry-run
python src/dashboard.py
```

Scrape and submit need `YC_EMAIL`/`YC_PASSWORD` (or cookies). Digest needs Gmail + HMAC + a real Worker URL in config.

If login fails, you get an email: update secrets or `credentials.local.yaml`, then **Actions → scan → Run workflow** (or wait up to 4 hours).

## Notes

- Login goes to `account.ycombinator.com` username/password (not the magic-link email page). Valid `YC_SESSION_COOKIES` are tried first. 2FA/CAPTCHA will email you.
- Each scan walks three WAAS listings from `search.sources`: remote engineering (`remote=only`), India (`locations=India`), and 1–2 years (`minExperience=1&minExperience=2`). Experience is not stacked onto the India/remote URLs. Same job URL from two feeds inserts once. If a filter looks wrong in the UI, copy the address bar into that source’s `url`.
- Approve is the only apply trigger. Scan (every 4 hours) and Reject never click Apply or Send.
- After Approve, `submit.yml` is live: click **Apply**, set the Gemini draft on the “about me” textarea (native input event so **Send** enables), click **Send**. Status becomes `submitted`.
- A prior `failed` apply can be retried by clicking Approve again on that digest card.
- Local testing: `python src/submit.py --job-id ID --dry-run` still fills and does not Send.
- Daily cap (`submit.daily_cap`, default 5) applies even after Approve.
- Scan, submit, and daily-report share concurrency group `jobs-db` (one git writer / report reader at a time, no cancel). Commit pulls `--rebase` and retries push so multiple Approves do not fail with `fetch first`.
- External/company-site apply listings are skipped and marked `failed` (error stored in `error_message`).
- Failed submits store `error_message` (truncated). Successful retries clear it.
- Daily report email (~10pm IST via `report.yml`) covers that IST calendar day: success/fail counts, failed jobs with errors, and identical errors grouped with counts. Manual: **Actions → daily-report → Run workflow** or `python src/daily_report.py`.
