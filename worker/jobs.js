import {
  decodeGithubContent,
  ghHeaders,
  githubError,
  isAuthed,
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

async function loadBlob(env, sha) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/git/blobs/${sha}`;
  const res = await fetch(url, { headers: ghHeaders(env) });
  if (!res.ok) throw new Error(githubError(res.status, await res.text()));
  const data = await res.json();
  if (!data.content) throw new Error("git blob has no content");
  return parseSnapshot(decodeGithubContent(data.content));
}

async function shaFromDataDir(env) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/contents/data`;
  const res = await fetch(url, { headers: ghHeaders(env) });
  if (!res.ok) throw new Error(githubError(res.status, await res.text()));
  const entries = await res.json();
  const entry = Array.isArray(entries) ? entries.find((item) => item.name === "jobs.json") : null;
  return entry?.sha || "";
}

async function loadSnapshot(env) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/contents/data/jobs.json`;
  const res = await fetch(url, { headers: ghHeaders(env) });
  if (res.status === 404) return emptySnapshot();
  const raw = await res.text();
  let meta = null;
  try {
    meta = JSON.parse(raw);
  } catch {
    meta = null;
  }
  if (res.ok && meta?.content) {
    return parseSnapshot(decodeGithubContent(meta.content));
  }
  if (res.ok && meta?.download_url) {
    const file = await fetch(meta.download_url, { headers: ghHeaders(env) });
    if (file.ok) return parseSnapshot(await file.text());
  }
  const sha = meta?.sha || (await shaFromDataDir(env));
  if (sha) return loadBlob(env, sha);
  if (res.status === 404) return emptySnapshot();
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
