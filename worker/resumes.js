import { credentialsMatch, isAuthed, sessionToken } from "./auth.js";
import {
  decodeGithubContent,
  esc,
  ghHeaders,
  githubError,
  loginPage,
  logoutForm,
  nav,
  page,
  redirect,
  safeNext,
  setCookie,
} from "./common.js";

const VARIANTS = ["fullstack", "backend", "frontend"];

function editorPage(files, notice = "", error = "") {
  const msg = error
    ? `<p class="error">${esc(error)}</p>`
    : notice
      ? `<p class="ok">${esc(notice)}</p>`
      : "";
  const areas = VARIANTS.map((name) => {
    const file = files[name] || { text: "", sha: "" };
    return `<label for="${name}">${name}.txt</label>
      <input type="hidden" name="sha_${name}" value="${esc(file.sha)}"/>
      <textarea id="${name}" name="${name}" spellcheck="false">${esc(file.text)}</textarea>`;
  }).join("\n");
  return page(
    "Resume editor",
    `${nav("resumes")}
     <h1>Resume editor</h1>
     <p class="muted">Saves commit <code>data/resumes/*.txt</code> on GitHub. The next scan uses this text.</p>
     ${msg}
     <form method="post" action="/resumes">
       ${areas}
       <div class="row">
         <button type="submit">Save</button>
       </div>
     </form>
     ${logoutForm()}`
  );
}

function contentsUrl(env, name) {
  return `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/contents/data/resumes/${name}.txt`;
}

function encodeGithubContent(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

async function loadResumes(env) {
  const files = {};
  for (const name of VARIANTS) {
    const res = await fetch(contentsUrl(env, name), { headers: ghHeaders(env) });
    const raw = await res.text();
    if (!res.ok) {
      throw new Error(githubError(res.status, raw));
    }
    const data = JSON.parse(raw);
    files[name] = { text: decodeGithubContent(data.content), sha: data.sha || "" };
  }
  return files;
}

async function saveResumes(env, form) {
  const errors = [];
  for (const name of VARIANTS) {
    const text = form.get(name);
    const sha = form.get(`sha_${name}`) || "";
    if (text === null) {
      errors.push(`${name}.txt missing from form`);
      continue;
    }
    const res = await fetch(contentsUrl(env, name), {
      method: "PUT",
      headers: { ...ghHeaders(env), "content-type": "application/json" },
      body: JSON.stringify({
        message: "update resumes from worker UI",
        content: encodeGithubContent(text),
        sha,
      }),
    });
    if (res.ok) continue;
    const detail = await res.text();
    if (res.status === 409 && /same/i.test(detail)) continue;
    errors.push(`${name}.txt: ${githubError(res.status, detail)}`);
  }
  return errors;
}

export async function handleResumes(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const authed = await isAuthed(request, env);

  if (path === "/resumes/login" && request.method === "POST") {
    const form = await request.formData();
    const user = String(form.get("username") || "");
    const pass = String(form.get("password") || "");
    const next = safeNext(String(form.get("next") || "/resumes"));
    if (!(await credentialsMatch(env, user, pass))) {
      return loginPage("Invalid name or password.", next);
    }
    const token = await sessionToken(env);
    return redirect(next, { "Set-Cookie": setCookie(token) });
  }

  if (path === "/resumes/logout" && request.method === "POST") {
    return redirect("/resumes", { "Set-Cookie": setCookie("", true) });
  }

  if (!authed) {
    const updated = url.searchParams.get("updated") || "";
    const notice =
      updated === "password"
        ? "Password updated. Log in with the new password."
        : updated === "name"
          ? "Name updated. Log in with the new name."
          : "";
    const next = path === "/resumes" ? "/resumes" : `${path}${url.search}`;
    return loginPage("", next, notice);
  }

  if (path === "/resumes" && request.method === "POST") {
    const form = await request.formData();
    const errors = await saveResumes(env, form);
    if (errors.length) {
      return redirect(`/resumes?error=${encodeURIComponent(errors.join(" "))}`);
    }
    return redirect("/resumes?saved=1");
  }

  if (path !== "/resumes" || request.method !== "GET") {
    return page("Not found", "<h1>Not found</h1>", 404);
  }

  try {
    const files = await loadResumes(env);
    const error = url.searchParams.get("error") || "";
    const notice = url.searchParams.has("saved") ? "Saved. Next scan will use this text." : "";
    return editorPage(files, notice, error);
  } catch (exc) {
    return editorPage(
      Object.fromEntries(VARIANTS.map((name) => [name, { text: "", sha: "" }])),
      "",
      String(exc.message || exc)
    );
  }
}
