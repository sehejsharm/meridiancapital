// The server's code fingerprint, taken at dashboard build time from the
// backend sources beside this folder. Must match backend/app/build.py
// code_fingerprint() byte for byte: the deck compares the two to tell
// whether the server runs the code this dashboard was built with.
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

const CODE_DIRS = ["app", "engine", "shared"];
const CODE_FILES = ["requirements.txt"];

function pyFiles(backend, dir) {
  const out = [];
  const walk = (abs) => {
    for (const name of readdirSync(abs)) {
      const p = path.join(abs, name);
      const st = statSync(p);
      if (st.isDirectory()) {
        if (name !== "__pycache__") walk(p);
      } else if (st.isFile() && name.endsWith(".py")) {
        out.push(path.relative(backend, p).split(path.sep).join("/"));
      }
    }
  };
  walk(path.join(backend, dir));
  return out;
}

/** 12 hex characters, or "" when the backend sources are not in this build. */
export function backendFingerprint(backend = path.resolve(process.cwd(), "..", "backend")) {
  try {
    if (!existsSync(backend)) return "";
    let files = [];
    for (const d of CODE_DIRS) {
      if (existsSync(path.join(backend, d))) files = files.concat(pyFiles(backend, d));
    }
    files = files.concat(CODE_FILES.filter((f) => existsSync(path.join(backend, f))));
    if (!files.length) return "";
    // Python sorts str by code point; for these ASCII paths that is byte order.
    files.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
    const h = createHash("sha256");
    for (const rel of files) {
      h.update(Buffer.concat([Buffer.from(rel, "utf8"), Buffer.from([0])]));
      h.update(Buffer.concat([readFileSync(path.join(backend, rel)), Buffer.from([0])]));
    }
    return h.digest("hex").slice(0, 12);
  } catch {
    return "";
  }
}
