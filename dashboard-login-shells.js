// dashboard-login-shells.js: the login shells the API started, which the web terminal may attach to.
//
// The terminal attaches only to a registered seat's tmux session. A login shell ("Connect" a provider, then "Open a
// terminal here") is a plain bash session with no registry row, so it was refused. The API's login-shell route lists
// each shell it starts in <data dir>/state/login-shells.json (api/src/lib/loginShells.ts), and removes it when the
// session ends or the entry expires. This only reads that list: a session is allowed when it is listed and not
// expired. Never a name pattern: a `login-*` session the API did not start is refused. It widens only the
// registered-session check; the authenticated-session check in front of it is unchanged.

"use strict";

const fs = require("fs");
const path = require("path");

const SESSION_NAME = /^[A-Za-z0-9_-]{1,80}$/;

// Read per upgrade, parsed again only when the file changes, so a burst of reconnects costs a stat.
let cache = { file: null, sig: null, shells: [] };

function loginShellsFile(orchestraDir) {
  return path.join(orchestraDir || "", "state", "login-shells.json");
}

function readShells(file) {
  let st;
  try { st = fs.statSync(file); } catch { cache = { file, sig: null, shells: [] }; return []; }
  // The API renames a new file into place, so the inode changes on every write even within one mtime tick.
  const sig = st.ino + ":" + st.mtimeMs + ":" + st.size;
  if (cache.file === file && cache.sig === sig) return cache.shells;
  let shells = [];
  try {
    const doc = JSON.parse(fs.readFileSync(file, "utf8"));
    shells = Array.isArray(doc && doc.shells) ? doc.shells : [];
  } catch { shells = []; }
  cache = { file, sig, shells };
  return shells;
}

/** Is `session` a login shell the API listed, and not yet expired? */
function listedLoginShell(session, orchestraDir, now) {
  if (!SESSION_NAME.test(session || "")) return false;
  const at = typeof now === "number" ? now : Date.now();
  return readShells(loginShellsFile(orchestraDir)).some((e) =>
    e && e.session === session && typeof e.expires_at === "string" && Date.parse(e.expires_at) > at);
}

module.exports = { listedLoginShell, loginShellsFile };
