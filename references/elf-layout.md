# ELF layout and the 2,953-byte budget

The builder emits a static, non-PIE, x86_64 `ET_EXEC` ELF with a single `PT_LOAD` segment and no section headers. The kernel only needs the ELF header and program headers; section headers would add ~192 bytes for no runtime benefit.

## File layout

```
offset  size  contents
0x0000    64  ELF header
0x0040    56  one program header (PT_LOAD)
0x0078     5  call rel32  (rel32 = payload + headers length)
0x007d     N  HTTP headers + Brotli/gzip payload   <- r12 points here
0x007d+N  C   server machine code
```

Total: `0x7d + N + C` = `125 + len(data) + len(code)`.

The `call` at the entry point pushes the address of the byte after it, then jumps over the data to the server code. `pop r12` in the server captures that address, which is exactly the start of the HTTP response. No relocation, no symbol table, no section table.

The data is never executed. The CPU lands on the `call`, jumps over the data, and loops in the server code after it.

## ELF header fields

| Field | Value |
| --- | --- |
| `e_ident` | `7f 45 4c 46 02 01 01 00` + 8 zero bytes |
| `e_type` | `ET_EXEC` (2) |
| `e_machine` | `EM_X86_64` (62) |
| `e_version` | 1 |
| `e_entry` | `0x400078` |
| `e_phoff` | `0x40` |
| `e_shoff` | 0 (no sections) |
| `e_ehsize` | 64 |
| `e_phentsize` | 56 |
| `e_phnum` | 1 |
| `e_shentsize`, `e_shnum`, `e_shstrndx` | 0 |

## Program header

| Field | Value |
| --- | --- |
| `p_type` | `PT_LOAD` (1) |
| `p_flags` | 7 (`RWE`) |
| `p_offset` | 0 |
| `p_vaddr` / `p_paddr` | `0x400000` |
| `p_filesz` / `p_memsz` | total ELF size |
| `p_align` | `0x1000` |

Mapping the file from offset 0 means the ELF headers are mapped too. That is valid and avoids a separate data segment. The segment is RWX because it contains code and data; a production binary would split permissions, but that costs bytes and an extra segment.

## Budget formula

```
max_payload = 2953
            - 120  (ELF header + program header)
            -   5  (call)
            - len(http_headers)
            - len(server_code)
```

Default build (`127.0.0.1`, drain on, no `Content-Length`):

```
2953 - 120 - 5 - 66 - 113 = 2649 bytes of compressed payload
```

The demo uses 2,616 B, so the ELF is `120 + 5 + 66 + 2616 + 113 = 2920` and 33 B remain. The QR is 99% of capacity.

Options:

| Option | Effect on the ELF |
| --- | ---: |
| `--no-drain` | -15 B |
| `--length` | +22 B |
| `--host 0.0.0.0` | -12 B |
| `--codec br` vs `gzip` | Brotli usually 15-20% smaller |

Headers are exactly:

```
HTTP/1.0 200 OK\r\n
Content-Type: text/html\r\n
Content-Encoding: br\r\n
\r\n
```

That is 66 bytes. There is no `Content-Length` by default: HTTP/1.0 closes the connection after the body, so the browser knows where it ends. `--length` adds `Content-Length: N\r\n` (22 bytes for a four-digit payload) when a strict client needs it.

The page's `<meta charset=utf-8>` supplies the charset; the header deliberately omits `; charset=utf-8` to save bytes. Do not remove the meta tag.

## Response extraction

`inspect`/`extract` find the `HTTP/1.0 200 OK` string, split at `\r\n\r\n`, and then decompress the body.

If `Content-Length` is present, the body is sliced to that length. Otherwise the decompressor is fed **one byte at a time** and stops when the stream reports finished. This matters because the ELF carries server code (and possibly section headers) immediately after the compressed stream; feeding all trailing bytes to a one-shot Brotli call fails. One-byte streaming stops exactly at the stream end and ignores everything after it.

This also lets the inspector read the original `qr-server` ELF: its response length includes 186 bytes of section headers after the Brotli stream, and its body is still recoverable.

## Inspecting and running

```bash
python scripts/asmweb.py inspect dist/index.elf --html dist/index.html
readelf -h -l dist/index.elf
objdump -D -b binary -m i386:x86-64 --adjust-vma=0x400000 dist/index.elf
```

`readelf` sees one `LOAD` segment and no sections; that is expected. `file` identifies it as a statically linked x86-64 ELF executable.

Run it only on Linux x86_64:

```bash
chmod +x dist/index.elf
./dist/index.elf
# http://127.0.0.1:8080
```

## Why not just use nasm/ld?

You can. The original project uses `nasm -f elf64`, `ld -N -s`, and `strip`, with `incbin` for the payload. That requires a Linux toolchain (or nasm + a linker on the host) and the layout is controlled by the linker.

This skill uses Keystone (a pip-installable assembler) and constructs the ELF with `struct`, so the same build runs on Windows, macOS, and Linux with no external toolchain. The generated layout is smaller than the original's because it omits section headers and a separate code/data arrangement.

If you prefer NASM, keep the same concepts:

```asm
_start:
    call server
data:
    db "HTTP/1.0 200 OK", 13, 10
    db "Content-Type: text/html", 13, 10
    db "Content-Encoding: br", 13, 10, 13, 10
    incbin "index.html.br"
data_end:
server:
    pop r12
    ; ...
    mov edx, data_end - data
    ; ...
```

Link with a single loadable segment and strip section headers if the size matters.
