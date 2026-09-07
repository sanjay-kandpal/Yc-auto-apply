# YC auto-apply

Human-gated job pipeline for [Work at a Startup](https://www.workatastartup.com): scrape listings, score them against your resume, draft an answer, email you a digest, and submit **only after you click Approve**.

Zero-cost stack: GitHub Actions + Cloudflare Worker + SQLite in this repo + Gmail SMTP + Groq/Gemini free tier.

Automated apply is likely against the site’s terms. Keep the approval gate and daily cap on. Never turn this into fully autonomous submit.

## What exists

| Path | Role |
|---|---|
| `src/scrape.py` | Playwright + session cookies. Intercepts Algolia / `companies/fetch` JSON and Inertia `data-page`, then stores new rows. |
| `src/match.py` | TF-IDF + keyword overlap + hard filters. Writes `match_score` and `resume_variant`. |
| `src/draft.py` | One Groq or Gemini call per job above threshold. |
| `src/email_digest.py` | HTML digest with HMAC Approve/Reject links. Sets `pending_approval`. |
| `worker/approve.js` | Verifies the link, fires GitHub `repository_dispatch`. |
| `src/submit.py` | Playwright fill + resume upload. Dry-run by default. Daily cap. |
| `src/notify.py` | Confirmation email after approve/reject/submit. |
| `src/dashboard.py` | Writes `docs/index.html` for GitHub Pages. |
| `src/db.py` | SQLite helpers. `python src/db.py --init` / `--mark-rejected ID`. |
| `scripts/export_session.py` | Headed login; prints `YC_SESSION_COOKIES`. |
| `config.yaml` | Filters, threshold, delays, cap, LLM provider, Worker URL. |
| `.github/workflows/scan.yml` | Cron 04:00 and 14:00 UTC plus manual run. |
| `.github/workflows/submit.yml` | Runs on `job_approved` / `job_rejected`. |

Status flow: `discovered` → `drafted` → `pending_approval` → `submitted` / `failed` / `rejected`. Below-threshold jobs stay `discovered` and never hit email.

## One-time setup

1. Python 3.12+: `pip install -r requirements.txt && python -m playwright install chromium`
2. Copy `.env.example` to `.env` (local only).
3. Replace `data/resumes/*.txt` with your real bullets (matching + LLM voice).
4. Add `data/resumes/fullstack.pdf`, `backend.pdf`, `ml.pdf` before any live submit.
5. Edit `config.yaml`: search URL (copy from the jobs board after you set filters), `email.approval_base_url`, `github.owner` / `github.repo`.
6. Edit `worker/wrangler.toml` `[vars]` `GH_OWNER` / `GH_REPO` to match.
7. `python src/db.py --init` (already done in a fresh clone if `data/jobs.db` exists).
8. `python scripts/export_session.py` while logged in; paste the blob into `.env` and into GitHub secrets.

### GitHub secrets (repo → Settings → Secrets)

| Secret | Purpose |
|---|---|
| `YC_SESSION_COOKIES` | base64 Playwright cookie JSON |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | digest + confirmation mail |
| `LLM_API_KEY` | Groq or Gemini (must match `draft.provider`) |
| `APPROVAL_HMAC_SECRET` | signs/verifies approval links (same value as the Worker secret) |
| `SUBMIT_DRY_RUN` | leave unset or `true` until you want live apply; set `false` to allow Send |

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

Scrape and submit need valid cookies. Digest needs Gmail + HMAC + a real Worker URL in config.

## Notes

- Re-export cookies when scrape reports a login wall.
- `SUBMIT_DRY_RUN` defaults to true: Approve still runs submit, fills the form, and does **not** click Send.
- Daily cap (`submit.daily_cap`, default 5) applies even after Approve.
- External/company-site apply listings are skipped and marked `failed`.
