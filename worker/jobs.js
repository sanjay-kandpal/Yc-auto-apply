import { isAuthed } from "./auth.js";
import {
  decodeGithubContent,
  ghFetch,
  githubError,
  loginPage,
  page,
} from "./common.js";
import { jobsPage } from "./jobs_ui.js";
import { buildListResponse, queryFromUrl } from "./jobs_query.js";

function emptySnapshot() {
  return {
    missing: true,
    exported_at: null,
    daily_cap: 5,
    submitted_today: 0,
    counts: {},
    jobs: [],
  };
}

function parseSnapshot(text) {
  const parsed = JSON.parse(text);
  return {
    missing: false,
    exported_at: parsed.exported_at || null,
    daily_cap: parsed.daily_cap ?? 5,
    submitted_today: parsed.submitted_today || 0,
    counts: parsed.counts || {},
    jobs: Array.isArray(parsed.jobs)
      ? parsed.jobs.map((job) => {
          const row = { ...job };
          delete row.approval_token;
          return row;
        })
      : [],
  };
}

function contentsUrl(env, path, ref) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/contents/${path}`;
  return ref ? `${url}?ref=${encodeURIComponent(ref)}` : url;
}

async function headCommitSha(env) {
  const branch = env.GH_BRANCH || "master";
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/commits/${encodeURIComponent(branch)}`;
  const res = await ghFetch(env, url);
  const raw = await res.text();
  if (!res.ok) throw new Error(githubError(res.status, raw));
  const data = JSON.parse(raw);
  if (!data.sha) throw new Error("GitHub commit has no sha");
  return data.sha;
}

async function loadBlob(env, sha) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/git/blobs/${sha}`;
  const res = await ghFetch(env, url, { Accept: "application/vnd.github.raw" });
  if (!res.ok) throw new Error(githubError(res.status, await res.text()));
  return parseSnapshot(await res.text());
}

async function shaFromDataDir(env, ref) {
  const res = await ghFetch(env, contentsUrl(env, "data", ref));
  if (!res.ok) throw new Error(githubError(res.status, await res.text()));
  const entries = await res.json();
  const entry = Array.isArray(entries) ? entries.find((item) => item.name === "jobs.json") : null;
  return entry?.sha || "";
}

async function loadSnapshot(env) {
  const commit = await headCommitSha(env);
  const res = await ghFetch(env, contentsUrl(env, "data/jobs.json", commit));
  if (res.status === 404) return emptySnapshot();
  const raw = await res.text();
  let meta = null;
  try {
    meta = JSON.parse(raw);
  } catch {
    meta = null;
  }
  if (!res.ok) throw new Error(githubError(res.status, raw));
  // jobs.json is >1MB so Contents omits `content`. Never use download_url
  // (raw.githubusercontent.com) — GitHub CDN serves a stale copy for hours.
  const sha = meta?.sha || (await shaFromDataDir(env, commit));
  if (sha) return loadBlob(env, sha);
  if (meta?.content) return parseSnapshot(decodeGithubContent(meta.content));
  throw new Error(githubError(res.status, raw));
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

export async function handleJobs(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const authed = await isAuthed(request, env);
  const next = `${path}${url.search}`;

  if (!authed) {
    if (path === "/resumes/jobs.json") {
      return jsonResponse({ error: "Login required" }, 401);
    }
    return loginPage("", next);
  }

  if (request.method !== "GET") {
    return page("Not found", "<h1>Not found</h1>", 404);
  }

  if (path === "/resumes/jobs") {
    return jobsPage();
  }

  if (path !== "/resumes/jobs.json") {
    return page("Not found", "<h1>Not found</h1>", 404);
  }

  try {
    const snapshot = await loadSnapshot(env);
    const query = queryFromUrl(url);
    if (query.id) {
      const job = (snapshot.jobs || []).find((row) => row.id === query.id);
      if (!job) return jsonResponse({ error: "Unknown job id" }, 404);
      return jsonResponse({
        missing: Boolean(snapshot.missing),
        exported_at: snapshot.exported_at || null,
        job,
      });
    }
    return jsonResponse(buildListResponse(snapshot, query));
  } catch (exc) {
    return jsonResponse({ error: String(exc.message || exc) }, 502);
  }
}
