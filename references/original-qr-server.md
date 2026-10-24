# The original qr-server, measured

The video is Gambiarra Labs' "现代前端有多臃肿？我用汇编把整个网站塞进了 3KB 二维码！" The project is [Xelckis/qr-server](https://github.com/Xelckis/qr-server), Apache-2.0, by Michel Leonardo / Gambiarra Labs.

I cloned the repository and decoded its QR instead of relying on the video description. These are the measured facts.

## What the original does

- `server.asm` (877 B of source) is a syscall-only x86_64 HTTP/1.0 server. It binds `0.0.0.0:8080`, accepts connections, writes one fixed response, closes, and loops.
- The page (`index.html`, 7,433 B) is Brotli-compressed to `index.html.br` (2,351 B). NASM's `incbin` embeds the `.br` file into the executable.
- The response is:

  ```
  HTTP/1.0 200 OK
  Content-Type: text/html
  Content-Encoding: br

  <Brotli stream>
  ```

  There is no `Content-Length`; HTTP/1.0 close delimits the body. The server does not read the request.
- The server code writes from a pointer obtained with `jmp`/`call`/`pop r12`: the response data follows the `call` at the end of the code, so the call's return address is the data start.
- The QR (`gambiarra_qr.png`) is generated with `qrencode -8 -l L -v 40 -s 4`. Byte mode (`-8`) is essential: text mode would corrupt nulls in the ELF.
- Build: `nasm -f elf64 -O9`, `ld -N -s --build-id=none -z norelro`, `strip`, `qrencode`.

## Measured numbers

| Item | Measured |
| --- | ---: |
| Extracted QR payload | **2,848 B** |
| QR ELF SHA-256 | `09bec30c93181a2de48ad74d94d0130bc876013fd939ce662970374c4ca3b579` |
| ELF entry | `0x400080` |
| Load segment | offset `0x80`, vaddr `0x400080`, filesz `0x9c9` (2,505 B), RWX |
| Section headers | 3 (`shoff 0xa60`), total 2,848 B |
| Code | ~88 B including the entry `jmp` and the `call` |
| Response length | 2,603 B (`mov dx, 0xa2b`) |
| HTTP headers | 66 B |
| Brotli payload in the repo | 2,351 B |
| Page after decompression | 7,433 B |

The README says "under 2,953 bytes", but the actual QR carries 2,848 bytes; it does not use the full v40-L capacity.

### A quirk: trailing bytes after the Brotli stream

The response length (2,603 B) is larger than the 66 B of headers plus the 2,351 B Brotli file. The extra 186 B are the ELF's section headers, which sit immediately after the payload. The server sends them; browsers ignore bytes after the end of a complete Brotli stream, so the page still works.

This is why `asmweb.py inspect` feeds the body to Brotli **one byte at a time** and stops when the stream finishes. A one-shot `brotli.decompress(body)` fails on those trailing bytes. The inspector recovers the original 7,433 B page exactly.

## The page is not fully offline

`index.html` includes:

```html
<script src=//unpkg.com/gsap/dist/gsap.min.js></script>
<script src=//unpkg.com/gsap/dist/Flip.min.js></script>
```

The server is zero-dependency, but the page needs network access for its GSAP/Flip animations. The demo's CSS uses system monospace/sans-serif stacks and a lot of box-drawing ASCII art.

This skill requires the page to be fully self-contained: no CDN, no fonts, no images, no external requests. The bundled `assets/example/index.html` uses CSS-only animation and a ~100 B clock script instead.

## What this skill does differently

| Area | qr-server (original) | asm-web (this skill) |
| --- | --- | --- |
| Layout | code → call → data | call → data → code |
| Section headers | present (3) | none |
| Response length | stale/hardcoded, includes trailing section headers | exact |
| Request read | none | drains up to 1 KB (`--no-drain` to skip) |
| Page | GSAP/Flip from unpkg | fully self-contained |
| Build | nasm + ld + strip + qrencode on Linux | Keystone + Python ELF builder, cross-platform |
| Verification | Go extractor, run natively | QR roundtrip, static inspect, Unicorn emulation, Linux CI |
| Demo size | 2,848 B ELF | 2,920 B ELF (with a larger, offline page) |

The technique is the same: a tiny syscall server plus browser-side Brotli decompression. The implementation here trades a few dozen bytes for portability, exact lengths, and safer inspection.

## Attribution

- Original project: [Xelckis/qr-server](https://github.com/Xelckis/qr-server), Apache License 2.0, by Michel Leonardo / Gambiarra Labs.
- Video: https://www.bilibili.com/video/BV1SBe56GEPQ/
- This skill is an independent implementation; its example page and build code are original. The original repository is referenced, not bundled.
