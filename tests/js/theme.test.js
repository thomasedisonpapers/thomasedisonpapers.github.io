// The theme a page opens in: the reader's saved choice, else the system's light or dark setting.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../../web/js/theme.js", import.meta.url), "utf-8");

// Runs theme.js against a pretend system setting and saved choice, and returns the page root and a way to flip the setting.
function open({ systemDark, saved = null, storageBlocked = false }) {
  const listeners = [];
  const system = { matches: systemDark, addEventListener: (type, fn) => listeners.push(fn) };
  const root = { dataset: {} };
  vm.runInNewContext(SOURCE, {
    document: { documentElement: root },
    window: { matchMedia: () => system },
    localStorage: { getItem: () => {
      if (storageBlocked) throw new Error("storage blocked");
      return saved;
    } },
  });
  const flip = (dark) => { system.matches = dark; listeners.forEach((fn) => fn()); };
  return { root, flip };
}

test("with no saved choice a dark system setting opens the site dark (regression: dark system ignored, smoke test 21 November)", () => {
  assert.equal(open({ systemDark: true }).root.dataset.theme, "dark");
});

test("with no saved choice a light system setting opens the site light", () => {
  assert.equal(open({ systemDark: false }).root.dataset.theme, "light");
});

test("a saved light choice outranks a dark system setting", () => {
  assert.equal(open({ systemDark: true, saved: "light" }).root.dataset.theme, "light");
});

test("a change of the system setting is followed while no choice is saved", () => {
  const page = open({ systemDark: false });
  page.flip(true);
  assert.equal(page.root.dataset.theme, "dark");
});

test("a manual theme survives changes to the system setting when storage is blocked", () => {
  const page = open({ systemDark: false, storageBlocked: true });
  page.root.dataset.themeOverride = "dark";
  page.root.dataset.theme = "dark";
  page.flip(true);
  page.flip(false);
  assert.equal(page.root.dataset.theme, "dark");
});
