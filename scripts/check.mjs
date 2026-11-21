// The one gate every change passes: builder and harvester tests, then search, security and page-policy tests.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
// The project's own environment when it exists, so local runs and CI use the same pinned packages.
const python = process.env.PYTHON
  || [join(root, ".venv", "Scripts", "python.exe"), join(root, ".venv", "bin", "python")].find((p) => existsSync(p))
  || "python3";

const steps = [
  ["Builder and harvester (pytest)", python, ["-m", "pytest", "-q", "-p", "no:randomly"]],
  // node expands the pattern itself; a bare directory is read as a file and fails.
  ["Search, security and page policy (node --test)", process.execPath, ["--test", "tests/js/**/*.test.js"]],
];

let failed = 0;
for (const [label, command, args] of steps) {
  console.log(`\n▶ ${label}`);
  const run = spawnSync(command, args, { cwd: root, stdio: "inherit", shell: false });
  if (run.status !== 0) {
    failed++;
    console.error(`✖ ${label} failed`);
  } else {
    console.log(`✔ ${label} passed`);
  }
}
if (failed) {
  console.error(`\n${failed} check(s) failed: this change is not ready.`);
  process.exit(1);
}
console.log("\nAll checks passed.");
