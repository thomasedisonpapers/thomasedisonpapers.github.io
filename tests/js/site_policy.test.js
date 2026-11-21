// Static rules every page and script must keep: a strict security policy, no inline code, no outside scripts.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

const WEB = new URL("../../web/", import.meta.url);
const pages = readdirSync(WEB).filter((name) => name.endsWith(".html"));
const scripts = readdirSync(new URL("js/", WEB)).filter((name) => name.endsWith(".js"));
const read = (path) => readFileSync(new URL(path, WEB), "utf-8");
const policyOf = (html) => (html.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/) || [])[1] || "";

test("the site has its pages", () => {
  assert.ok(pages.length >= 8);
});

for (const page of pages) {
  test(`${page} declares a content security policy`, () => {
    assert.notEqual(policyOf(read(page)), "");
  });

  test(`${page} runs only its own scripts`, () => {
    assert.match(policyOf(read(page)), /script-src 'self';/);
  });

  test(`${page} never allows eval or inline scripts`, () => {
    assert.doesNotMatch(policyOf(read(page)), /unsafe-eval|script-src[^;]*unsafe-inline/);
  });

  test(`${page} refuses plugins and other base addresses`, () => {
    assert.match(policyOf(read(page)), /object-src 'none'.*base-uri 'self'/);
  });

  test(`${page} has no inline script`, () => {
    assert.doesNotMatch(read(page), /<script(?![^>]*\bsrc=)[^>]*>/i);
  });

  test(`${page} has no inline event handler`, () => {
    assert.doesNotMatch(read(page), /\son[a-z]+\s*=/i);
  });

  test(`${page} loads scripts only from its own folder`, () => {
    assert.doesNotMatch(read(page), /<script[^>]*src="(https?:)?\/\//i);
  });
}

for (const script of scripts) {
  test(`js/${script} never evaluates strings as code`, () => {
    assert.doesNotMatch(read(`js/${script}`), /\beval\s*\(|new Function\s*\(|setTimeout\s*\(\s*["'`]/);
  });

  test(`js/${script} writes no inline event handler into markup`, () => {
    assert.doesNotMatch(read(`js/${script}`), /<[a-z][^>`]*\son[a-z]+=/i);
  });

  test(`js/${script} never uses document.write`, () => {
    assert.doesNotMatch(read(`js/${script}`), /document\.write\s*\(/);
  });
}

const css = read("css/site.css");
// A rule's declarations, found by its selector at the start of a line.
const rule = (selector) => {
  const at = css.indexOf(`\n${selector} {`);
  return at < 0 ? "" : css.slice(at, css.indexOf("}", at));
};

test("the filter sidebar has no scrollbar of its own, so the wheel always scrolls the page (regression: awkward filters, 21 November)", () => {
  assert.doesNotMatch(rule(".refine"), /overflow|max-height/);
});

test("filter lists shrink to the panel instead of growing to their longest name (regression: 21 November)", () => {
  assert.match(rule(".refine fieldset"), /min-inline-size: 0/);
});

test("a long filter name is cut with an ellipsis rather than widening the panel", () => {
  assert.match(rule(".refine label.opt .opt-label"), /text-overflow: ellipsis/);
});

test("the filter sidebar never grows wider than its column (regression: horizontal scroll in Refine results, 21 November)", () => {
  assert.match(rule(".refine"), /min-width: 0/);
});

test("only the search bar's submit button is painted blue, never its scope menu (regression: unreadable Everything menu, 21 November)", () => {
  assert.equal(rule(".searchbar button"), "");
});

// The site must work unchanged at any address, so a later move from github.io to a domain breaks nothing.
const BUILDER = new URL("../../builder/", import.meta.url);
const ownFiles = [...pages, ...scripts.map((s) => `js/${s}`), "css/site.css"];
const ROOTED = /(?:href|src|action)=["']\/(?!\/)|fetch\(\s*["'`]\/(?!\/)|url\(\s*["']?\/(?!\/)/;
const HOSTED = /github\.io|pages\.dev|gitlab\.io|netlify\.app/;

for (const file of ownFiles) {
  test(`${file} links only by relative paths, so the site works at any address or folder`, () => {
    assert.doesNotMatch(read(file), ROOTED);
  });

  test(`${file} never names the site's own host`, () => {
    assert.doesNotMatch(read(file), HOSTED);
  });
}

for (const file of readdirSync(BUILDER).filter((name) => name.endsWith(".py"))) {
  test(`builder/${file} never names the site's own host`, () => {
    assert.doesNotMatch(readFileSync(new URL(file, BUILDER), "utf-8"), HOSTED);
  });
}
