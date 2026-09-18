#!/usr/bin/env node
// Distributes process configuration from process/ into .github/: Issue and pull request
// templates as files, labels as gh commands to review and run by hand.
//
// Why labels are only PRINTED, not applied: a label is a repository setting on GitHub's side,
// not a file in the tree. This script has no credentials and shouldn't have any — it prints the
// commands so they can be reviewed before anything touches the remote repository.
//
// StafffingCalculator is a monorepo — one GitHub repository, one label set, one Issue-form set.
// The one place a monorepo still needs two variants is the pull request template (backend
// invariants vs. frontend invariants); GitHub supports that natively via a template PICKER
// (.github/PULL_REQUEST_TEMPLATE/*.md, chosen from the "Preview" dropdown when opening a PR, or
// via ?template=<name>.md&expand=1), so both ship side by side instead of one repo picking one.
//
// Deliberately does not touch .github/workflows/ — a change to the CI pipeline is a decision
// with consequences; it goes through code review, not through a sync run.
//
// Usage:
//   node tools/sync-github.mjs              copies templates, reports what changed
//   node tools/sync-github.mjs --check      reports only drift, writes nothing (exit 1 on drift)
//   node tools/sync-github.mjs --labels     prints gh commands for labels (bash), writes nothing
//   node tools/sync-github.mjs --labels-ps1 same, for PowerShell
//
// Run from the repository root.

import { readdirSync, readFileSync, writeFileSync, mkdirSync, existsSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const CHECK_ONLY = process.argv.includes("--check");
const LABELS_BASH = process.argv.includes("--labels");
const LABELS_PWSH = process.argv.includes("--labels-ps1");
const LABELS_ONLY = LABELS_BASH || LABELS_PWSH;

// ---------------------------------------------------------------------------- CONFIGURATION
const OWNER = "TanerCRB";
const REPO = "StafffingCalculator";

const TEMPLATE_DIR = resolve("process", "issue-templates");
const LABEL_MANIFEST = resolve("process", "labels.json");

// Issue forms (everything except the PR templates and config.yml) land under
// .github/ISSUE_TEMPLATE/. The two PR templates land under .github/PULL_REQUEST_TEMPLATE/,
// picked by name at PR-creation time. config.yml also lands under ISSUE_TEMPLATE/.
const PR_TEMPLATES = {
  "PULL_REQUEST_TEMPLATE.md": ".github/PULL_REQUEST_TEMPLATE/backend.md",
  "PULL_REQUEST_TEMPLATE-frontend.md": ".github/PULL_REQUEST_TEMPLATE/frontend.md",
};
// ---------------------------------------------------------------------------------------------

function fail(message) {
  console.error(`ERROR: ${message}`);
  process.exit(2);
}

// Both shells use single quotes for the literal, but escape an apostrophe inside it
// differently: bash closes and reopens the quote, PowerShell doubles the apostrophe.
function shellQuote(value) {
  return LABELS_PWSH
    ? `'${String(value).replaceAll("'", "''")}'`
    : `'${String(value).replaceAll("'", `'\\''`)}'`;
}

// ---------------------------------------------------------------------------- labels

if (LABELS_ONLY) {
  if (!existsSync(LABEL_MANIFEST)) fail(`not found: ${LABEL_MANIFEST}.`);

  let manifest;
  try {
    manifest = JSON.parse(readFileSync(LABEL_MANIFEST, "utf8"));
  } catch (error) {
    fail(`${LABEL_MANIFEST} is not valid JSON: ${error.message}`);
  }

  const labels = [];
  const seen = new Set();
  for (const [group, value] of Object.entries(manifest)) {
    if (group.startsWith("$") || !value?.labels) continue;
    for (const label of value.labels) {
      for (const field of ["name", "color", "description"]) {
        if (!label?.[field]) fail(`a label in group "${group}" is missing field "${field}".`);
      }
      if (seen.has(label.name)) fail(`label "${label.name}" is defined more than once.`);

      // GitHub rejects a description over 100 characters with a 422 at creation time, not here —
      // catching it before the gh call is cheaper than discovering it mid-run.
      if (label.description.length > 100) {
        fail(`label "${label.name}": description is ${label.description.length} chars, GitHub's limit is 100.`);
      }

      // Pure ASCII, enforced by an assertion, not by trust — see the comment in labels.json.
      for (const field of ["name", "description"]) {
        const offending = [...label[field]].find((c) => c.charCodeAt(0) > 127);
        if (offending) {
          fail(
            `label "${label.name}", field "${field}": character outside ASCII ` +
              `"${offending}" (U+${offending.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}). ` +
              `The label manifest must be pure ASCII.`,
          );
        }
      }
      if (!/^[0-9a-f]{6}$/.test(label.color)) {
        fail(`label "${label.name}": color "${label.color}" is not a six-character hex value without #.`);
      }
      seen.add(label.name);
      labels.push(label);
    }
  }

  if (labels.length === 0) fail("the manifest contains no labels.");

  if (LABELS_PWSH) {
    console.log("# Generated by tools/sync-github.mjs --labels-ps1. Review before running.");
    console.log("# --force overwrites the color and description of an existing label, so the run is idempotent.");
    console.log("#");
    console.log("# Run the WHOLE file at once, not line by line:");
    console.log("#   Invoke-Expression (Get-Content <file> -Raw)");
    console.log("#");
    console.log("# We deliberately do NOT set $ErrorActionPreference = 'Stop': gh writes to stderr");
    console.log("# even on success, which Windows PowerShell 5.1 can turn into a NativeCommandError.");
    console.log("# Every call is independent and idempotent (--force).");
    console.log("");
  } else {
    console.log("#!/usr/bin/env bash");
    console.log("# Generated by tools/sync-github.mjs --labels. Review before running.");
    console.log("# --force overwrites the color and description of an existing label, so the run is idempotent.");
    console.log("set -euo pipefail");
    console.log("");
  }

  console.log(`# ---- ${REPO} (${labels.length} labels) ----`);
  for (const label of labels) {
    console.log(
      `gh label create ${shellQuote(label.name)} --color ${shellQuote(label.color)} ` +
        `--description ${shellQuote(label.description)} --repo ${OWNER}/${REPO} --force`,
    );
  }
  console.log(`\n# Calls emitted: ${labels.length}.`);
  process.exit(0);
}

// ---------------------------------------------------------------------------- templates

if (!existsSync(TEMPLATE_DIR) || !statSync(TEMPLATE_DIR).isDirectory()) {
  fail(`not found: ${TEMPLATE_DIR}.`);
}

const allFiles = readdirSync(TEMPLATE_DIR)
  .filter((f) => f.endsWith(".yml") || f.endsWith(".md"))
  .sort();

if (allFiles.length === 0) fail(`directory ${TEMPLATE_DIR} is empty.`);

// An Issue form without name/description/body is rejected by GitHub at render time, not commit
// time — it simply stops appearing in the picker, with no error anywhere.
for (const file of allFiles) {
  if (PR_TEMPLATES[file] || file === "config.yml") continue;
  const body = readFileSync(join(TEMPLATE_DIR, file), "utf8");
  for (const field of ["name", "description", "body"]) {
    if (!new RegExp(`^${field}:`, "m").test(body)) {
      fail(`${file}: Issue form is missing field "${field}".`);
    }
  }
}

let drift = 0;
let copied = 0;

const normalise = (text) => (text === null ? null : text.replace(/\r\n/g, "\n"));

function writeOne(sourceFile, destRelative) {
  const source = readFileSync(join(TEMPLATE_DIR, sourceFile), "utf8");
  const destination = resolve(destRelative);
  const current = existsSync(destination) ? readFileSync(destination, "utf8") : null;
  if (normalise(current) === normalise(source)) return;

  drift++;
  const what = current === null ? "new" : "changed";
  if (CHECK_ONLY) {
    console.log(`DRIFT ${destRelative} (${what})`);
  } else {
    mkdirSync(join(destination, ".."), { recursive: true });
    writeFileSync(destination, source);
    copied++;
    console.log(`WROTE ${destRelative} (${what})`);
  }
}

for (const file of allFiles) {
  if (PR_TEMPLATES[file]) {
    writeOne(file, PR_TEMPLATES[file]);
  } else {
    writeOne(file, join(".github", "ISSUE_TEMPLATE", file));
  }
}

// A form removed from the source stays in the picker until removed by hand at the target.
const issueDir = resolve(".github", "ISSUE_TEMPLATE");
if (existsSync(issueDir)) {
  const orphans = readdirSync(issueDir).filter(
    (f) => (f.endsWith(".yml") || f.endsWith(".md")) && !allFiles.includes(f),
  );
  for (const orphan of orphans) {
    drift++;
    console.log(`ORPHANED .github/ISSUE_TEMPLATE/${orphan} — no source, remove by hand`);
  }
}
const prDir = resolve(".github", "PULL_REQUEST_TEMPLATE");
if (existsSync(prDir)) {
  const known = new Set(Object.values(PR_TEMPLATES).map((p) => p.split("/").pop()));
  const orphans = readdirSync(prDir).filter((f) => f.endsWith(".md") && !known.has(f));
  for (const orphan of orphans) {
    drift++;
    console.log(`ORPHANED .github/PULL_REQUEST_TEMPLATE/${orphan} — no source, remove by hand`);
  }
}

if (CHECK_ONLY) {
  console.log(drift === 0 ? "No drift." : `Drifted: ${drift}.`);
  process.exit(drift === 0 ? 0 : 1);
}

console.log(
  copied === 0
    ? `No changes. Templates: ${allFiles.length}.`
    : `Wrote ${copied} of ${allFiles.length} templates. Files are tracked by git — review and commit them yourself.`,
);
