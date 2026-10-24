---
name: asm-web
description: Build a minimal, self-contained animated website served by a hand-written x86_64 assembly HTTP server, then pack the entire ELF into one version-40-L QR code (2,953-byte hard limit). Use whenever the user mentions 汇编写网站, 汇编 HTTP 服务器, 最小体积网站, 3KB 网站, 二维码里的 ELF, qr-server, size coding, asm web, tiny web server, ELF in QR, or wants to fit a page/ELF into a QR. Also use for extracting, inspecting, or emulating such a QR/ELF safely.
---

# asm-web

Build a page → Brotli-compress it → embed it in a tiny x86_64 Linux ELF with a syscall-only HTTP server → render the ELF as one QR code. The reference demo is a 7.9 KB animated page, a 2,616 B Brotli payload, 113 B of assembly, and a **2,920 B ELF** with 33 B of headroom in a v40-L QR.

The key trick: **the server never decompresses anything.** It sends `Content-Encoding: br` (or gzip) and the browser decompresses the body. A decompressor in assembly would never fit; letting the browser do it is what makes the budget possible.

## Quick start

```bash
python scripts/asmweb.py doctor
python scripts/asmweb.py size assets/example/index.html
python scripts/asmweb.py build assets/example/index.html -o dist --v40 --minify --emulate
python scripts/asmweb.py extract dist/index.png -o dist/extracted.elf --html dist/extracted.html
python scripts/asmweb.py inspect dist/index.elf
python scripts/asmweb.py emulate dist/index.elf --page dist/index.page.html
```

`build` writes `index.elf`, `index.page.html` (the exact served page), `index.png`, `index.svg`, and `index.json` (sizes, hashes, layout, QR metadata). It verifies the embedded page and the QR roundtrip itself; `--emulate` additionally executes the server in Unicorn and checks the HTTP response.

On Linux, run it natively:

```bash
chmod +x dist/index.elf
./dist/index.elf            # http://127.0.0.1:8080
```

On Windows there is no native x86_64 Linux execution; build and `--emulate` are the verification path, and the GitHub Actions workflow runs the ELF on Ubuntu.

## Workflow

1. **Write the page.** One HTML file, all CSS/JS inline, no images, no web fonts, no CDN. The page must be UTF-8. Read `references/animation-playbook.md` for effects that compress well.
2. **Measure before building.** `size` reports the source, the minified source, Brotli q11 and gzip -9, then computes the exact ELF size and headroom. Judge the compressed payload, not raw HTML.
3. **Shrink if needed.** Remove base64 blobs, images, fonts, third-party scripts, and unused CSS/JS. Use `--minify`, short class names, and CSS-only animation. Re-run `size`; keep 30-50 B of headroom if the page may be edited again.
4. **Build.** Use `--v40` for the dense version-40 look. `--minify` is optional; compare both. The builder assembles `assets/server.asm` with Keystone, so nasm/ld are not required.
5. **Verify.** Build-time checks already compare the embedded page and decode the QR. `--emulate` proves the assembly path: socket → bind → listen → accept → read → write → close. On Linux, also run the ELF and fetch `/`.
6. **Inspect before running anything.** `extract` only writes bytes; `inspect` parses the ELF, finds the HTTP response, decompresses the page, and disassembles the server. Never execute an unknown QR payload.
7. **Report** the source size, payload, ELF total, headroom, QR version, asm bytes, and the local URL. Do not paste the page or the ELF as base64 into chat.

## The byte budget

The ELF must fit a version 40, ECC L QR: **2,953 bytes** total.

| Part | Default |
| --- | ---: |
| ELF header + program header | 120 B |
| `call` over the data | 5 B |
| HTTP headers (`Content-Type`, `Content-Encoding`, no length/charset) | 66 B |
| server asm (with request drain) | 113 B |
| **overhead before the page** | **304 B** |
| maximum Brotli payload | 2,649 B |
| demo payload | 2,616 B |
| **demo ELF** | **2,920 B (33 B headroom)** |

Options that move the budget:

- `--no-drain` saves 15 B (skips the request read; see the RST note below).
- `--length` costs 22 B (adds `Content-Length`; useful for strict clients).
- `--host 0.0.0.0` saves 12 B of asm but exposes the server to the LAN.
- `--codec gzip` usually costs 15-20% more than Brotli; only use it for compatibility or a very small page.

`size` computes the exact number for the current page and flags; trust it over this table.

## Layout

```
0x400000  ELF header (64) + program header (56)
0x400078  E8 rel32  -> call jumps over the response data to the server
0x40007d  HTTP headers + Brotli payload     <- r12 points here
   ...    server code: pop r12; socket; bind; listen; accept; read; write; close; loop
```

One `PT_LOAD` covers the whole file (offset 0, vaddr 0x400000, RWX, filesz = total). There are no section headers; the kernel only needs the program header. The call's return address is the data start, so the code gets the response pointer without a relocation. Full details and field values: `references/elf-layout.md`.

The assembly is deliberately small and ignore-errors:

- It binds a `sockaddr_in` built on the stack.
- It writes one fixed response per accepted connection and closes; HTTP/1.0 end-of-stream delimits the body (no `Content-Length` by default).
- It reads up to 1 KB before replying to drain the request, so `close()` sends FIN instead of RST. `--no-drain` removes this.
- It does not parse the request, handle errors, set `SO_REUSEADDR`, or support keep-alive. It is a local demo, not a production server.

The syscall table, register conventions, sockaddr layout, and NASM equivalents are in `references/x86_64-linux-syscalls.md`.

## Hard rules

- **2,953 bytes is the cap.** Never claim a build fits without a successful `build` and (when available) `emulate`. If the page is over, cut content; do not split the QR or silently change the QR version beyond 40.
- **The page must be self-contained and UTF-8.** External `<script src>`, `<link>`, fonts, or images break the promise and often the budget. `size` warns about them.
- **A QR is public.** Compression is not encryption; never encode credentials, private URLs, or personal data.
- **Never run a decoded ELF blindly.** The original project's QR contains an executable; running an unknown one is remote code execution. `extract` writes bytes; `inspect` is static; `emulate` stays inside Unicorn. Run only binaries you built or have reviewed, preferably in a VM/container.
- **Bind to loopback by default.** `--host 0.0.0.0` exposes the page on the network; use it only on request and say so.
- **This is not a production server.** No TLS, no auth, no request parsing, no concurrency, no error handling, no keep-alive. Say so when handing it over.

## If it does not work

- **Over budget:** run `size`; it prints the exact breakdown. Cut the page (playbook), try `--minify`, `--no-drain`, or `--host 0.0.0.0`. There is no capacity beyond v40-L.
- **Assembly error:** `assets/server.asm` must use Intel syntax and NASM-style comments. The builder strips `;` lines for Keystone; labels and `jmp` work. Check that every `{{...}}` placeholder was replaced.
- **Build/emulate pass but native Linux fails:** check x86_64, a free port, `chmod +x`, and the firewall. If responses truncate, the read drain is missing; rebuild without `--no-drain`. If bind fails, the port is in use; rebuild with another `--port`.
- **QR decode fails:** pass the PNG (not SVG), keep the 4-module quiet zone, use `--scale 8` or larger. `zxing-cpp` and Pillow are required for the decode check.
- **`inspect` says capstone missing:** `python -m pip install capstone`.
- **`emulate` says unicorn missing:** `python -m pip install unicorn`. Unicorn 2.1.4 does not need manual RIP adjustment for syscall hooks; older versions might.
- **Minify breaks layout:** the minifier leaves `pre`, `textarea`, `script`, `style`, and `svg` untouched but collapses text whitespace elsewhere. Build without `--minify` if the change matters.

## If the user wants an MCP

Expose `build`, `size`, `inspect`, `extract`, and `emulate` as MCP tools around `scripts/asmweb.py`. Do not expose a blocking `serve` tool: the ELF is a process, not a request/response API; start it as a background process and return the URL plus how to stop it.

## References

- `references/animation-playbook.md` — byte-cheap effects, compression, and the demo's numbers. Read when designing or shrinking a page.
- `references/elf-layout.md` — exact ELF fields, sizes, the call/pop trick, extraction math. Read when changing the server or debugging size.
- `references/x86_64-linux-syscalls.md` — syscall numbers, sockaddr_in, ABI, NASM/Keystone notes. Read before modifying `assets/server.asm`.
- `references/original-qr-server.md` — what the Gambiarra Labs project actually does, measured from its repo and QR, and how this skill differs.
