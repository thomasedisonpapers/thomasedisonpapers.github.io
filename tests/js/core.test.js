import assert from "node:assert/strict";
import test from "node:test";

const OLD = "1234567890abcdef";
const NEW = "abcdef1234567890";

async function checkMissingFiles(t, current, paths) {
  let reloads = 0;
  let pointerRequests = 0;
  globalThis.location = {
    host: "example.test",
    href: "https://example.test/archive/search.html?q=edison",
    reload: () => { reloads++; },
  };
  t.after(() => { delete globalThis.location; });
  t.mock.method(globalThis, "fetch", async (url, options) => {
    if (url === "data/current.json") {
      pointerRequests++;
      assert.equal(options.cache, "no-store");
      return Response.json({ revision: current });
    }
    return new Response(null, { status: 404 });
  });
  const { fetchJSON } = await import(`../../web/js/core.js?current=${current}`);
  const requests = paths.map((path) => assert.rejects(fetchJSON(path), /404/));
  await Promise.all(requests);
  return { reloads, pointerRequests };
}

test("missing files from an old deployment reload a tab once", async (t) => {
  const paths = [`data/${OLD}/index/edi.json`, `data/${OLD}/index/let.json`];
  assert.deepEqual(await checkMissingFiles(t, NEW, paths), { reloads: 1, pointerRequests: 1 });
});

test("a missing file in the current deployment does not reload the tab", async (t) => {
  const result = await checkMissingFiles(t, OLD, [`data/${OLD}/index/edi.json`]);
  assert.equal(result.reloads, 0);
});
