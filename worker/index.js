import { handleApprove } from "./approve.js";
import { handleJobs } from "./jobs.js";
import { handleResumes } from "./resumes.js";

function stripSlash(pathname) {
  return pathname.replace(/\/+$/, "") || "/";
}

export default {
  async fetch(request, env) {
    const path = stripSlash(new URL(request.url).pathname);
    if (path === "/resumes/jobs" || path === "/resumes/jobs.json") {
      return handleJobs(request, env);
    }
    if (path === "/resumes" || path.startsWith("/resumes/")) {
      return handleResumes(request, env);
    }
    return handleApprove(request, env);
  },
};
