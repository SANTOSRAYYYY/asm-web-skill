---
name: asm-web
description: Build a minimal, self-contained animated website served by a hand-written x86_64 assembly HTTP server, then pack the entire ELF into one version-40-L QR code (2,953-byte hard limit). Also builds personal sites from a profile with four animated themes (terminal, brutalist, editorial, blueprint) and a text portrait generated from a photo. Use whenever the user mentions 汇编写网站, 汇编 HTTP 服务器, 最小体积网站, 3KB 网站, 二维码里的 ELF, qr-server, personal website, 个人网站, 文字肖像, ASCII portrait, size coding, asm web, tiny web server, ELF in QR, or wants to fit a page/ELF into a QR. Also use for extracting, inspecting, or emulating such a QR/ELF safely.
---

# asm-web

Build a page → Brotli-compress it → embed it in a tiny x86_64 Linux ELF with a syscall-only HTTP server → render the ELF as one QR code. The reference demo is a 7.9 KB animated page, a 2,616 B Brotli payload, 113 B of assembly, and a **2,920 B ELF** with 33 B of headroom in a v40-L QR.

The key trick: **the server never decompresses anything.** It sends `Content-Encoding: br` (or gzip) and the browser decompresses the body. A decompressor in assembly would never fit; letting the browser do it is what makes the budget possible.

## Quick start

Engine demo:

```bash
python scripts/asmweb.py doctor
python scripts/asmweb.py size assets/example/index.html
python scripts/asmweb.py build assets/example/index.html -o dist --v40 --minify --emulate
python scripts/asmweb.py extract dist/index.png -o dist/extracted.elf --html dist/extracted.html
python scripts/asmweb.py inspect dist/index.elf
python scripts/asmweb.py emulate dist/index.elf --page dist/index.page.html
```

Personal site:

```bash
python scripts/asmweb.py themes
python scripts/asmweb.py portrait assets/example/portrait.png --style stencil --width 64 --out portrait.html
python scripts/asmweb.py personal assets/example/profile.json --theme terminal -o dist/personal --emulate
```

`build` writes `index.elf`, `index.page.html` (the exact served page), `index.png`, `index.svg`, and `index.json`. `personal` writes the same artifacts named after the theme. Both verify the embedded page and the QR roundtrip; `--emulate` additionally executes the server in Unicorn and checks two connections.

On Linux, run it natively:

```bash
chmod +x dist/personal/terminal/terminal.elf
./dist/personal/terminal/terminal.elf     # http://127.0.0.1:8080
```

On Windows there is no native x86_64 Linux execution; build and `--emulate` are the verification path, and the GitHub Actions workflow runs the ELF on Ubuntu.

## Personal sites

`asmweb.py personal PROFILE --theme NAME` renders a profile into one of four complete pages, generates the text portrait, then runs the normal ELF/QR build. The page is still one self-contained HTML file; the profile is content, not a rigid layout.

| Theme | Motion | Example ELF | Headroom |
| --- | --- | ---: | ---: |
| `terminal` | boot overlay, scanlines, glitch, cursor, portrait scan | 2,890 B | 63 B |
| `brutalist` | marquee, staggered slam, hover invert, portrait stamp | 2,879 B | 74 B |
| `editorial` | fade/rise, slow portrait drift, underline links | 2,908 B | 45 B |
| `blueprint` | scan line, clip reveal, clock | 2,833 B | 120 B |

All four fit a v40-L QR with the example profile and a stencil portrait. They show **three featured projects**; the profile can list more with `"featured": true` on the three that matter. A simple 5-project page can fit with a lighter layout, but the four animated themes are curated for three. Do not promise all five inside them.

Profile essentials:

- Required: `name`, `handle`, `role`, `tagline`, `bio`.
- Optional: `about` paragraphs, `location`, `status`, `links[]`, `projects[]`, `skills[]`.
- `projects[]` items: `title`, `desc`, `url`, `featured` (boolean).
- `links[]` items: `label`, `url` (http/https/mailto/#), `note`.
- `portrait`: path relative to the profile; `portrait_style` (`stencil`/`ascii`/`block`/`braille`), `portrait_width`, `portrait_threshold`, `portrait_invert`.
- URLs are escaped and restricted to safe schemes. The page must stay self-contained: no images, web fonts, CDNs, or external requests.

Intake (two short rounds, not a 20-field form):

1. **Content depth**: animated 3-project theme, or a larger custom page? The four themes trade full portfolios for motion.
2. **Audience + CTA**: who visits, and what should they do?
3. **Theme/motion**: terminal / brutalist / editorial / blueprint. Show the previews and byte costs.
4. **Portrait**: photo or high-contrast image? Stylized likeness acceptable? If not, use a silhouette/abstract image.

Then collect name/handle/role/tagline, bio + about, three featured projects, links, status/location, language, and privacy constraints. Keep the user's voice; never invent links or claims. Explain what will not fit: photos, fonts, many projects, long bilingual copy, and heavy decoration together.

Text portraits:

- The portrait is generated locally from a photo/abstract image as text; the QR is public, so get consent for a face.
- `stencil` is the default: 1-bit `#`, best recognizability per byte (~190-260 B compressed). `ascii` is more "text art"; `block` can muddy faces; `braille` is small and font-dependent.
- A bitmap + JS renderer was measured and is worse than pre-rendered text; do not add it.
- Use `aria-hidden` (the templates do) and keep the name/role as real text.
- Tune `--threshold`, width (48-72), and `--portrait-invert` for light themes; preview before building.

Iterate with `--render-only`; it writes the exact page and reports the ELF size without producing a QR. Open `dist/personal/<theme>/<theme>.page.html` in a browser to see the animation. `personal` uses the page as-is by default: **do not add `--minify`** unless you measured both; sitegen already minifies CSS, and the HTML minifier can increase the Brotli payload.

A normal QR scanner sees binary; use `extract --html` or `inspect` to recover the page, or run the ELF on Linux. A portable browser viewer/PWA is not part of this build yet.

Full details: `references/personal-site-playbook.md`.

## Workflow (engine)

1. **Write the page.** One HTML file, all CSS/JS inline, no images, no web fonts, no CDN. UTF-8. For personal sites, start from a theme/profile and customise; read `references/animation-playbook.md` for effects that compress well.
2. **Measure before building.** `size` reports the source, the minified source, Brotli q11 and gzip -9, then computes the exact ELF size and headroom. For personal sites use `personal --render-only`. Judge the compressed payload, not raw HTML.
3. **Shrink if needed.** Remove base64 blobs, images, fonts, third-party scripts, and unused CSS/JS. Use short class names and CSS-only animation. Re-run the measurement; keep 30-50 B of headroom if the page may be edited again.
4. **Build.** Use `--v40` for the dense version-40 look when the user wants it; otherwise let segno choose the smallest version. `--minify` is optional; compare. The builder assembles `assets/server.asm` with Keystone, so nasm/ld are not required.
5. **Verify.** Build-time checks compare the embedded page and decode the QR. `--emulate` proves the assembly path: socket → bind → listen → accept → read → write → close. On Linux, also run the ELF and fetch `/`.
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
| engine demo payload | 2,616 B |
| **engine demo ELF** | **2,920 B (33 B headroom)** |

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

- **2,953 bytes is the cap.** Never claim a build fits without a successful `build`/`personal` and (when available) `emulate`. If the page is over, cut content; do not split the QR or silently change the QR version beyond 40.
- **The page must be self-contained and UTF-8.** External `<script src>`, `<link>`, fonts, or images break the promise and often the budget. `size` warns about them.
- **A QR is public.** Compression is not encryption; never encode credentials, private URLs, personal data, or a portrait without consent. A text portrait is still a likeness.
- **Personal themes show three featured projects.** Do not silently drop projects the user expects to see. If they need all five, use a custom lighter page or a larger non-QR site, and say so.
- **Never run a decoded ELF blindly.** The original project's QR contains an executable; running an unknown one is remote code execution. `extract` writes bytes; `inspect` is static; `emulate` stays inside Unicorn. Run only binaries you built or have reviewed, preferably in a VM/container.
- **Bind to loopback by default.** `--host 0.0.0.0` exposes the page on the network; use it only on request and say so.
- **This is not a production server.** No TLS, no auth, no request parsing, no concurrency, no error handling, no keep-alive. Say so when handing it over.

## If it does not work

- **Over budget:** run `size`/`personal --render-only`; it prints the exact breakdown. Cut the page (playbook), try `--no-drain`, or `--host 0.0.0.0`. There is no capacity beyond v40-L.
- **Assembly error:** `assets/server.asm` must use Intel syntax and NASM-style comments. The builder strips `;` lines for Keystone; labels and `jmp` work. Check that every `{{...}}` placeholder was replaced.
- **Personal render fails:** check the profile fields, the portrait path (relative to the profile), and the theme name. The CSS guard rejects unbalanced braces/control characters in a theme.
- **Personal page changes size unexpectedly:** the HTML minifier can help or hurt; measure with and without `--minify`. Sitegen always minifies CSS.
- **Portrait is unrecognizable:** use a high-contrast front-facing crop, adjust width (48-72) and threshold, try stencil instead of block/braille; a silhouette or abstract image is valid.
- **Build/emulate pass but native Linux fails:** check x86_64, a free port, `chmod +x`, and the firewall. If responses truncate, the read drain is missing; rebuild without `--no-drain`. If bind fails, the port is in use; rebuild with another `--port`.
- **QR decode fails:** pass the PNG (not SVG), keep the 4-module quiet zone, use `--scale 8` or larger. `zxing-cpp` and Pillow are required for the decode check.
- **`inspect` says capstone missing:** `python -m pip install capstone`.
- **`emulate` says unicorn missing:** `python -m pip install unicorn`. Unicorn 2.1.4 does not need manual RIP adjustment for syscall hooks; older versions might.
- **Minify breaks layout:** the minifier leaves `pre`, `textarea`, `script`, `style`, and `svg` untouched but collapses text whitespace elsewhere. Build without `--minify` if the change matters.

## If the user wants an MCP

Expose `build`, `personal`, `portrait`, `themes`, `size`, `inspect`, `extract`, and `emulate` as MCP tools around `scripts/asmweb.py`. Do not expose a blocking `serve` tool: the ELF is a process, not a request/response API; start it as a background process and return the URL plus how to stop it.

## References

- `references/personal-site-playbook.md` — profile schema, four themes, text portraits, intake, byte costs. Read for personal-site work.
- `references/animation-playbook.md` — byte-cheap effects, compression, and the demo's numbers. Read when designing or shrinking a page.
- `references/elf-layout.md` — exact ELF fields, sizes, the call/pop trick, extraction math. Read when changing the server or debugging size.
- `references/x86_64-linux-syscalls.md` — syscall numbers, sockaddr_in, ABI, NASM/Keystone notes. Read before modifying `assets/server.asm`.
- `references/original-qr-server.md` — what the Gambiarra Labs project actually does, measured from its repo and QR, and how this skill differs.
