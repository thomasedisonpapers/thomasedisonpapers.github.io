// Turns search queries into vectors off the page's thread, with the model and revision the host used for the documents.
let extractor = null;

async function start(setup) {
  const { env, pipeline } = await import(`${setup.vendor}transformers-4.3.2/transformers.min.js`);
  env.allowLocalModels = false;
  // The runtime comes from the site itself, since the page's policy allows no outside scripts.
  env.backends.onnx.wasm.wasmPaths = {
    mjs: `${setup.vendor}onnxruntime-web-1.31.0-dev.20260914/ort-wasm-simd-threaded.mjs`,
    wasm: `${setup.vendor}onnxruntime-web-1.31.0-dev.20260914/ort-wasm-simd-threaded.wasm`,
  };
  env.backends.onnx.wasm.numThreads = 1;
  env.backends.onnx.wasm.proxy = false;
  return pipeline("feature-extraction", setup.model, { revision: setup.revision, dtype: setup.dtype, device: "wasm" });
}

self.onmessage = async ({ data }) => {
  const { id, text, setup } = data;
  try {
    if (!extractor) extractor = start(setup);
    const model = await extractor;
    const output = await model(`${setup.prefix}${text}`, { pooling: "cls", normalize: true });
    const vector = Float32Array.from(output.data);
    self.postMessage({ id, vector }, [vector.buffer]);
  } catch (error) {
    extractor = null;
    self.postMessage({ id, error: String((error && error.message) || error) });
  }
};
