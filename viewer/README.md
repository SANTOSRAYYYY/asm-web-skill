# ASM/WEB Viewer

A static browser viewer for asm-web QR codes. It decodes the QR to an ELF, extracts the embedded HTTP response, decompresses the Brotli payload, and renders the page in a sandboxed iframe.

**The ELF is never executed.**

## Live

https://santosrayyyy.github.io/asm-web/

The viewer is a PWA: open it on a phone, tap **扫描二维码**, and point the camera at an asm-web QR. Camera access needs HTTPS (GitHub Pages) or localhost. Upload and drag/drop work without a camera.

## What it supports

- QR camera scan (jsQR, 1600 px frames)
- QR image upload/drag/drop
- Direct `.elf` file upload/drag/drop
- The bundled demo QR (`demo-qr.png`, terminal personal site)
- Download the decoded ELF or HTML
- Fullscreen, sandboxed page rendering

## Security model

- The QR is treated as untrusted data. It is decoded to bytes; the ELF is parsed, never executed.
- The page is inserted into `<iframe sandbox="allow-scripts">` without `allow-same-origin`.
- The iframe HTML gets `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src data:` so the page cannot make external requests, load remote assets, submit forms, or navigate the top window.
- The Brotli WASM decoder is part of the viewer, not from the QR.
- Downloaded files are data; inspect an ELF before running it on Linux.

## Run locally

```bash
cd viewer
python -m http.server 8088
# open http://localhost:8088/
```

Camera works on `localhost`. Opening `index.html` via `file://` may block camera access; use the server or the hosted PWA.

## Build

```bash
npm ci
npm run build
```

`npm run build` bundles `src/main.js` into `viewer.js` and emits `assets/brotli_dec_wasm_bg.wasm`. The built files are committed so GitHub Pages can serve them without a Node build step.

## Deploy

`.github/workflows/pages.yml` deploys the `viewer/` directory to GitHub Pages on changes. The Pages URL is expected to be `https://santosrayyyy.github.io/asm-web/`.

## Flow

```
camera / image / ELF file
  -> jsQR raw bytes (or ELF read)
  -> find HTTP/1.x 200 response inside the ELF
  -> Content-Encoding: br -> brotli-dec-wasm (byte-stream, stops before server code)
  -> HTML -> sandboxed iframe + CSP
```

## Credits

- [jsQR](https://github.com/cozmo/jsQR) — Apache-2.0
- [brotli-dec-wasm](https://github.com/ustclug-dev/brotli-dec-wasm) — MIT OR Apache-2.0
- [esbuild](https://esbuild.github.io/) — MIT (build only)

See [THIRD_PARTY.md](THIRD_PARTY.md).
