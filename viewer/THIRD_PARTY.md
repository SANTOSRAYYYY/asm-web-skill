# Third-party licenses

The viewer bundles the following libraries. Their license notices are also kept at the end of `viewer.js` by esbuild (`--legal-comments=eof`).

- **jsQR 1.4.0** — Apache-2.0 — https://github.com/cozmo/jsQR
- **brotli-dec-wasm 2.3.2** — MIT OR Apache-2.0 — https://github.com/ustclug-dev/brotli-dec-wasm

The QR decoder and Brotli WASM run locally in the browser. The decoded ELF is never executed.
