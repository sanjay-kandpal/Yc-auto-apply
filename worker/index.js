import { handleApprove } from "./approve.js";
import { handleResumes } from "./resumes.js";

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/resumes" || path.startsWith("/resumes/")) {
      return handleResumes(request, env);
    }
    return handleApprove(request, env);
  },
};
