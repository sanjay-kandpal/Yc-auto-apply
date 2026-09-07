function b64url(bytes) {
  let bin = "";
  const arr = new Uint8Array(bytes);
  for (let i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function b64urlDecode(text) {
  const padded = text + "=".repeat((4 - (text.length % 4)) % 4);
  const bin = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function sign(jobId, action, expiry, secret) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const msg = new TextEncoder().encode(`${jobId}|${action}|${expiry}`);
  return b64url(await crypto.subtle.sign("HMAC", key, msg));
}

async function verify(jobId, action, expiry, token, secret) {
  const exp = Number(expiry);
  if (!jobId || !token || !secret) return false;
  if (!Number.isFinite(exp) || exp < Math.floor(Date.now() / 1000)) return false;
  if (action !== "approve" && action !== "reject") return false;
  const expected = await sign(jobId, action, exp, secret);
  const a = b64urlDecode(expected);
  const b = b64urlDecode(token);
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

function html(title, body, status = 200) {
  return new Response(
    `<!doctype html><html><body style="font-family:sans-serif;padding:40px">
     <h1>${title}</h1><p>${body}</p></body></html>`,
    { status, headers: { "content-type": "text/html; charset=utf-8" } }
  );
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const jobId = url.searchParams.get("job_id") || "";
    const action = url.searchParams.get("action") || "";
    const expiry = url.searchParams.get("expiry") || "";
    const token = url.searchParams.get("token") || "";

    const ok = await verify(jobId, action, expiry, token, env.APPROVAL_HMAC_SECRET);
    if (!ok) {
      return html("Link invalid", "This approval link is invalid or expired.", 400);
    }

    const eventType = action === "approve" ? "job_approved" : "job_rejected";
    const gh = await fetch(
      `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GH_PAT_FOR_DISPATCH}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "yc-job-bot",
        },
        body: JSON.stringify({
          event_type: eventType,
          client_payload: { job_id: jobId },
        }),
      }
    );

    if (!gh.ok) {
      const detail = await gh.text();
      return html("GitHub error", `Could not start the workflow (${gh.status}). ${detail}`, 502);
    }

    if (action === "approve") {
      return html("Approved", "Application will be submitted shortly (subject to the daily cap).");
    }
    return html("Rejected", "This listing will be marked rejected.");
  },
};
