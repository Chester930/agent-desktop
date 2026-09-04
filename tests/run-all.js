const { spawnSync } = require("node:child_process");

const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const result = spawnSync(npm, ["test"], {
  cwd: process.cwd(),
  stdio: "inherit",
  shell: process.platform === "win32",
});

if (result.error) {
  console.error(`Unable to start npm test: ${result.error.message}`);
  process.exit(1);
}

process.exit(result.status ?? 1);
