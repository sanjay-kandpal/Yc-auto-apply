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

function parseVersions(text) {
  try {
    const parsed = JSON.parse(text);
    return parsed.versions && typeof parsed.versions === "object" ? parsed.versions : {};
  } catch {
    return {};
  }
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

async function loadRawBlob(env, sha) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/git/blobs/${sha}`;
  const res = await ghFetch(env, url, { Accept: "application/vnd.github.raw" });
  if (!res.ok) throw new Error(githubError(res.status, await res.text()));
  return res.text();
}

async function shaFromDataDir(env, ref, fileName) {
  const res = await ghFetch(env, contentsUrl(env, "data", ref));
  if (!res.ok) throw new Error(githubError(res.status, await res.text()));
  const entries = await res.json();
  const entry = Array.isArray(entries) ? entries.find((item) => item.name === fileName) : null;
  return entry?.sha || "";
}

async function loadDataFile(env, commit, fileName) {
  const res = await ghFetch(env, contentsUrl(env, `data/${fileName}`, commit));
  if (res.status === 404) return null;
  const raw = await res.text();
  let meta = null;
  try {
    meta = JSON.parse(raw);
  } catch {
    meta = null;
  }
  if (!res.ok) throw new Error(githubError(res.status, raw));
  const sha = meta?.sha || (await shaFromDataDir(env, commit, fileName));
  if (sha) return loadRawBlob(env, sha);
  if (meta?.content) return decodeGithubContent(meta.content);
  throw new Error(githubError(res.status, raw));
}

async function loadSnapshot(env) {
  const commit = await headCommitSha(env);
  const jobsText = await loadDataFile(env, commit, "jobs.json");
  if (!jobsText) return { snapshot: emptySnapshot(), versions: {} };
  const snapshot = parseSnapshot(jobsText);
  let versions = {};
  try {
    const versionsText = await loadDataFile(env, commit, "resume_versions.json");
    if (versionsText) versions = parseVersions(versionsText);
  } catch {
    versions = {};
  }
  return { snapshot, versions };
}

function newestHashForVariant(versions, variant) {
  let best = "";
  let bestAt = "";
  for (const [hash, row] of Object.entries(versions || {})) {
    if (!row || row.variant !== variant) continue;
    const at = String(row.created_at || "");
    if (at >= bestAt) {
      bestAt = at;
      best = hash;
    }
  }
  return best;
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
    const { snapshot, versions } = await loadSnapshot(env);
    const query = queryFromUrl(url);
    if (query.id) {
      const job = (snapshot.jobs || []).find((row) => row.id === query.id);
      if (!job) return jsonResponse({ error: "Unknown job id" }, 404);
      const hash = job.resume_version_hash || "";
      const resume = hash && versions[hash] ? { hash, ...versions[hash] } : null;
      const live_resume_hash = newestHashForVariant(versions, job.resume_variant || "");
      return jsonResponse({
        missing: Boolean(snapshot.missing),
        exported_at: snapshot.exported_at || null,
        job,
        resume,
        live_resume_hash: live_resume_hash || null,
      });
    }
    return jsonResponse(buildListResponse(snapshot, query));
  } catch (exc) {
    return jsonResponse({ error: String(exc.message || exc) }, 502);
  }
}
