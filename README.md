# YC auto-apply

Human-gated job pipeline for [Work at a Startup](https://www.workatastartup.com): scrape listings, score them against your resume, draft an answer, email you a digest, and submit **only after you click Approve**.

Zero-cost stack: GitHub Actions + Cloudflare Worker + SQLite in this repo + Gmail SMTP + Gemini free tier (OpenRouter `:free` fallback).

Automated apply is likely against the site’s terms. Keep the approval gate and daily cap on. Never turn this into fully autonomous submit.

**Current architecture plan (modules, schema, workflows, secrets):** [plan.md](plan.md)

**Deep product guide (architecture, every module, incidents, backlog):** [docs/YC-auto-apply-product-guide.docx](docs/YC-auto-apply-product-guide.docx)

**Production hardening and free path toward ~100 users:** [docs/YC-auto-apply-production-scale.docx](docs/YC-auto-apply-production-scale.docx)

## What exists

| Path | Role |
|---|---|
| `src/login.py` | Playwright email/password login. On failure, emails you to update credentials; next 4-hour run (or manual scan) retries. |
| `src/scrape.py` | Playwright after login. Walks `search.sources` (remote, India, 1–2 years). Intercepts Algolia / `companies/fetch` JSON and Inertia `data-page`, then stores new rows (dedup by URL). |
| `src/match.py` | TF-IDF + keyword overlap + hard filters. Writes `match_score` and `resume_variant`. |
| `src/draft.py` | Gemini (OpenRouter fallback) drafts notes, validates prompt rules (sentence count, no greeting/sign-off, first person, GitHub line), and re-asks up to `validation_retries` times. |
| `src/email_digest.py` | HTML digest with HMAC Approve/Reject links. Sets `pending_approval`. |
| `worker/index.js` | Cloudflare Worker router: `/` approve links, `/resumes` editor, `/resumes/jobs` DB viewer, forgot password/name. |
| `worker/approve.js` | Verifies the HMAC link, fires GitHub `repository_dispatch`. |
| `worker/auth.js` | KV login credentials, session nonce, OTP hash/tries. Seed name `dev` until reset. |
| `worker/forgot.js` | Forgot password/name: email → 6-digit OTP → set new value. |
| `worker/resumes.js` | Password login + hosted editor; commits `data/resumes/*.txt` via GitHub Contents API. |
| `worker/jobs.js` | Read-only jobs visualizer; loads `data/jobs.json` via GitHub Contents API. |
| `src/export_jobs.py` | Writes `data/jobs.json` (all job columns except `approval_token`). |
| `src/submit.py` | After Approve only: click Apply → fill LLM note → Send. Daily cap. Local `--dry-run` skips Send. |
| `src/session.py` | Playwright browser/context. Login is unrecorded; if `RECORD_RUN=true`, a second context records scrape/submit. |
| `src/record_video.py` | Concatenate Playwright `.webm` clips, transcode to one H.264 mp4, write `meta.json`. |
| `src/publish_release.py` | Attach the mp4 to a `recording-<run_id>` GitHub Release (`GITHUB_TOKEN`). |
| `src/prune_recordings.py` | Delete `recording-*` releases older than `spectate.retain_hours` (24) and over `keep_releases`. |
| `src/spectate_email.py` | Scan recap email with Release download link and/or Actions artifact link. |
| `src/notify.py` | Confirmation email after approve/reject/submit (includes stored error on failure, plus recording links when present). |
| `src/daily_report.py` | 10pm IST daily email over the previous 10pm→10pm window: applied/failed counts, failed jobs, errors grouped by identical message. |
| `src/resume_otp_email.py` | Emails a 6-digit resume-login OTP (Gmail SMTP). Never logs the code. |
| `src/log_config.py` | Stdout `logging` for Actions (`LOG_LEVEL`, default INFO). |
| `src/dashboard.py` | Writes `docs/index.html` for GitHub Pages and `data/jobs.json` for the Worker viewer. |
| `src/db.py` | SQLite helpers. Migrates `error_message` and `sent_message` on connect. `python src/db.py --init` / `--mark-rejected ID`. |
| `scripts/export_session.py` | Optional cookie fallback if password login is blocked (OAuth / 2FA). |
| `scripts/commit_state.sh` | Shared Actions commit/push with conflict recovery (restore DB + regenerate dashboard and `jobs.json`). |
| `.github/actions/setup-cached-python` | Shared Actions setup: restore `.venv` (and Playwright browsers on scan/submit) or install on cache miss. |
| `.github/actions/publish-recording` | Follow-on job: ffmpeg merge, mp4 artifact, GitHub Release, prune, recording email. |
| `config.yaml` | Filters, threshold, delays, cap, LLM provider, Worker URL, spectate bitrate/retention. |
| `.github/workflows/scan.yml` | Every 4 hours (`0 */4 * * *`) plus manual Run workflow. |
| `.github/workflows/submit.yml` | Runs on `job_approved` / `job_rejected`. |
| `.github/workflows/report.yml` | ~10pm IST (`30 16 * * *` UTC) plus manual Run workflow — emails the daily report. |
| `.github/workflows/resume-otp.yml` | `repository_dispatch` `resume_otp` — emails the 6-digit resume-login code. |
| `.github/workflows/prune-recordings.yml` | Hourly (`20 * * * *`) plus manual — delete `recording-*` Releases older than 24 hours. |

Status flow: `discovered` → `drafted` → `pending_approval` → `submitted` / `failed` / `rejected`. Below-threshold jobs stay `discovered` and never hit email.

## One-time setup

1. Python 3.12+: `pip install -r requirements.txt && python -m playwright install chromium`
2. Copy `.env.example` to `.env` **or** `credentials.local.yaml.example` to `credentials.local.yaml` and put your Work at a Startup email/password there. Never commit those files.
3. Update resume bullets via the hosted editor (`https://yc-job-approve.sanjaykandpal4.workers.dev/resumes`, seed name `dev`) **or** edit `data/resumes/*.txt` locally. They are used for matching, Gemini drafts, and the apply message. PDFs are not required.
4. Edit `config.yaml`: `search.sources` URLs (copy from the jobs board after you set filters), `email.approval_base_url`, `github.owner` / `github.repo`.
5. Edit `worker/wrangler.toml` `[vars]` `GH_OWNER` / `GH_REPO` to match.
6. `python src/db.py --init` (already done in a fresh clone if `data/jobs.db` exists).
7. Set `YC_EMAIL` and `YC_PASSWORD` (preferred). Cookie export is only a fallback.

### GitHub secrets (repo → Settings → Secrets)

| Secret | Purpose |
|---|---|
| `YC_EMAIL` / `YC_PASSWORD` | auto-login on every scan/submit |
| `YC_SESSION_COOKIES` | optional cookie fallback |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | digest + confirmation + scan recording + daily report + resume-login OTP |
| `LLM_API_KEY` | Gemini (primary drafts) |
| `OPENROUTER_API_KEY` | OpenRouter free-tier fallback (`google/gemma-4-31b-it:free`) |
| `APPROVAL_HMAC_SECRET` | signs/verifies approval links (same value as the Worker secret) |
| `SUBMIT_DRY_RUN` | unused on GitHub now (`submit.yml` forces live Send after Approve). Use `--dry-run` locally only. |

### Cloudflare Worker

```bash
cd worker
npx wrangler login
npx wrangler kv namespace create RESUME_AUTH
# paste the id into wrangler.toml [[kv_namespaces]] binding RESUME_AUTH
npx wrangler secret put APPROVAL_HMAC_SECRET
npx wrangler secret put GH_PAT_FOR_DISPATCH
npx wrangler secret put RECOVERY_EMAIL
npx wrangler deploy
```

The PAT needs `repo` scope so it can send `repository_dispatch` (approve/reject and resume OTP), commit resume files from `/resumes`, and read `data/jobs.json` for the Jobs viewer. Put the Worker URL into `email.approval_base_url`. `RECOVERY_EMAIL` must be the same address as `GMAIL_ADDRESS`.

Resume editor: `https://yc-job-approve.sanjaykandpal4.workers.dev/resumes` — seed name `dev` (change via **Forgot name?**). Password starts as the code seed until you use **Forgot password?**. After save, the next scan uses the new `.txt` files. Already-scored or drafted jobs are not re-matched.

Forgot password / name: on the login page, enter `RECOVERY_EMAIL`, wait for the 6-digit Gmail code (`resume-otp.yml`, 30s–2min), then set a new password or name. Codes expire in 10 minutes. New values live in Cloudflare KV (`RESUME_AUTH`); a credential change logs out existing sessions. Keep the repo private — the OTP is in the Actions event payload until it expires.

Jobs visualizer (same login): `https://yc-job-approve.sanjaykandpal4.workers.dev/resumes/jobs` — read-only snapshot of `data/jobs.json` from the last scan/submit commit. The Worker loads the **latest commit** on `GH_BRANCH` via the Git blobs API (not GitHub’s cached raw CDN). Lists **10 rows per page**; `/resumes/jobs.json?page=N` returns only that page (plus `total_pages`). Click page numbers or Next to fetch the next 10. Deploy the Worker after pulling Worker changes (`npx wrangler deploy` in `worker/`).

Run recordings: GitHub **Releases** tagged `recording-<run_id>`. They are deleted after **24 hours** (`spectate.retain_hours`; hourly `prune-recordings.yml` plus after each spectate job). A count cap (`keep_releases`) is a backup. The email link downloads the mp4 (GitHub does not play it inline) and 404s after prune. You must be logged into GitHub if the repo is private. Workflow artifacts expire after 1 day.

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

Local spectate (optional, needs ffmpeg): `RECORD_RUN=true python src/submit.py --job-id ID --dry-run` writes `.webm` clips under `RECORDING_DIR` or `data/recordings/`. Login typing is not recorded.

Scrape and submit need `YC_EMAIL`/`YC_PASSWORD` (or cookies). Digest needs Gmail + HMAC + a real Worker URL in config.

If login fails, you get an email: update secrets or `credentials.local.yaml`, then **Actions → scan → Run workflow** (or wait up to 4 hours).

## Notes

- Login goes to `account.ycombinator.com` username/password (not the magic-link email page). Valid `YC_SESSION_COOKIES` are tried first. 2FA/CAPTCHA will email you.
- Each scan walks three WAAS listings from `search.sources`: remote engineering (`remote=only`), India (`locations=India`), and 1–2 years (`minExperience=1&minExperience=2`). Experience is not stacked onto the India/remote URLs. Same job URL from two feeds inserts once. If a filter looks wrong in the UI, copy the address bar into that source’s `url`.
- Approve is the only apply trigger. Scan (every 4 hours) and Reject never click Apply or Send.
- After Approve, `submit.yml` is live: click **Apply**, set the Gemini draft on the “about me” textarea (native input event so **Send** enables), click **Send**. Every sent note includes `github.profile_url` (`https://github.com/sanjay-kandpal`). Status becomes `submitted`.
- A prior `failed` apply can be retried by clicking Approve again on that digest card.
- Local testing: `python src/submit.py --job-id ID --dry-run` still fills and does not Send.
- Daily cap (`submit.daily_cap`, default 5) applies even after Approve.
- Scan, submit, and daily-report share concurrency group `jobs-db` (one writer at a time, no cancel). Encoding/upload runs in a follow-on `spectate` job **outside** that group so ffmpeg does not block Approve. Commit uses `scripts/commit_state.sh`: on rebase/push conflict it resets to remote, restores this run’s `data/jobs.db`, regenerates `docs/index.html` and `data/jobs.json`, and retries with exponential backoff.
- Spectate: Actions sets `RECORD_RUN=true`. Playwright records scrape/submit after login, a follow-on job merges to mp4 (artifact, 1 day), publishes a `recording-<run_id>` GitHub Release, and emails the Release URL plus the Actions run URL. Releases older than 24 hours are deleted (`spectate.retain_hours`, hourly `prune-recordings.yml` and after each spectate job). Failed runs still publish whatever video exists. Recordings are not committed. Default `RECORD_RUN` is off locally. The Release link downloads the file and 404s after prune; keep the repo private.
- Resume editor lives on the Worker (`/resumes`), not GitHub Pages. Save writes `data/resumes/*.txt` through the GitHub Contents API. Login is name + password (seed `dev` until you reset). **Forgot password?** / **Forgot name?** email a 6-digit code to `RECOVERY_EMAIL` via `resume-otp.yml` (always `GMAIL_ADDRESS`). The Jobs viewer is `/resumes/jobs` (same cookie): 10 rows per page from `/resumes/jobs.json?page=`, not the full snapshot. It reads the latest `GH_BRANCH` commit through the Git blobs API so the table is not stuck on a cached `raw.githubusercontent.com` copy.
- Actions Python deps are cached via `.github/actions/setup-cached-python`. Cache key is OS + Python version + `requirements.txt` hash. Hit → skip `pip install` and reuse `.venv`. Miss → create venv, install, save cache. Scan/submit also cache `~/.cache/ms-playwright`; on a browser cache hit they only install OS deps (`playwright install-deps`). Changing `requirements.txt` or the Python patch version forces a fresh install.
- External/company-site apply listings are skipped and marked `failed` (error stored in `error_message`).
- Failed submits store `error_message` (truncated). Successful retries clear it. The exact apply note (draft + GitHub line, or resume fallback) is stored in `sent_message` on submit, dry-run fill, and failed apply.
- Daily report email (~10pm IST via `report.yml`) covers the previous **10pm→10pm IST** window (not midnight→now). If scan holds the `jobs-db` lock past midnight, the delayed run still uses last night’s 10pm close so that day’s applies are not dropped. Manual: **Actions → daily-report → Run workflow** (optional date input) or `python src/daily_report.py` / `--date YYYY-MM-DD`.
- Pipeline Python modules log to stdout via `src/log_config.py` (Actions job logs). INFO for milestones, WARNING for retries/fallbacks, ERROR with traceback for submit/LLM failures. Per-job scrape/match lines are DEBUG. Set `LOG_LEVEL=DEBUG` locally. Emails and `error_message` are unchanged. `scripts/commit_state.sh` still uses `echo`.
