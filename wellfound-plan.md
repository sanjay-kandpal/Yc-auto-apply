# Wellfound Job Apply Automation — Current Plan

Human-gated pipeline for [Wellfound](https://wellfound.com) (formerly AngelList Talent): scrape → score → draft → email digest → Approve runs **live Send**: Learn more → read modal JD → LLM note → Apply → fill → Send application.

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
│   ├── scan-wellfound.yml    # 02:00/10:00/18:00 UTC (8h, 2h off YC)
│   └── submit-wellfound.yml  # Approve: live apply + spectate; Reject: mark rejected
├── src/wellfound/
│   ├── login.py / session.py / parse.py / filters.py / scrape.py
│   ├── apply_probe.py        # Form field classifier helpers
│   ├── apply_flow.py         # Learn more → JD → Apply → fill → Send application
│   └── submit.py             # Orchestrates live apply (cap, draft, persist)
├── src/
│   ├── db.py                 # source + apply_kind; submitted_today(source=)
│   ├── draft.py              # draft_note_for_job() for apply-time LLM
│   ├── match.py / email_digest.py / notify.py
└── config.yaml               # wellfound.submit.enabled: true
```

---

## 2. Data model

```sql
source TEXT       -- yc | wellfound
apply_kind TEXT   -- cover_letter_only | has_questions | eligibility_blocked | external_ats | unknown | null
```

**Funnel on Approve:**
- Learn more → read `#job-description` → `draft_note_for_job` (resume variant + `with_github`)
- Apply → if single answer textarea only → fill → **Send application** → `submitted`
- Extra questions / eligibility / external / UI mismatch → `failed` + email notify

Daily cap: `submitted_today(conn, source="wellfound")` vs `wellfound.submit.daily_cap`.

---

## 3. Live apply DOM

1. `button[data-test="LearnMoreButton"]`
2. Read `#job-description` (“About the job”)
3. Dialog Apply (`button` Apply / `data-test="Button"`)
4. `textarea[name^="customQuestionAnswers"][name$="[answer]"]`
5. `button[data-test="JobApplicationModal--SubmitButton"]` — Send application

`--dry-run` fills but does not click Send. `wellfound.submit.enabled: false` refuses Send.

---

## 4. Workflows / secrets

| Workflow | Role |
|---|---|
| `scan-wellfound.yml` | scrape → match → draft → digest |
| `submit-wellfound.yml` | Reject / live Submit + LLM keys + spectate |

Secrets: `WELLFOUND_*`, shared Gmail/HMAC, `LLM_API_KEY` / `OPENROUTER_API_KEY`.

---

## 5. Build status

| Milestone | Status |
|---|---|
| Scrape / match / digest / HMAC | Done |
| Eligibility skip keywords | Done |
| `apply_kind` classifier helpers | Done |
| Live Send (Learn more → Send application) | Done |

**Local smoke:** unit tests → scrape/match/draft/digest → `python src/wellfound/submit.py --job-id ID --headed` (or `--dry-run` first).
