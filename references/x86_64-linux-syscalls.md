# Minimal x86_64 Linux HTTP server

This is the syscall-level knowledge needed to modify `assets/server.asm` without breaking the byte budget.

## Calling convention

Linux x86_64 system calls use:

| Register | Meaning |
| --- | --- |
| `rax` | syscall number |
| `rdi` | arg 1 |
| `rsi` | arg 2 |
| `rdx` | arg 3 |
| `r10` | arg 4 |
| `r8` | arg 5 |
| `r9` | arg 6 |
| `rcx`, `r11` | clobbered by the `syscall` instruction |
| return | `rax` (negative values are `-errno`) |

The kernel preserves all other registers. The demo relies on that: it keeps the listening socket in `rdi`, the client socket in `r8`, and the response pointer in `r12` across syscalls.

## Syscall numbers used

| Number | Name | Arguments | Returns |
| ---: | --- | --- | --- |
| 0 | `read` | `rdi=fd, rsi=buf, rdx=count` | bytes read, 0 on EOF, negative error |
| 1 | `write` | `rdi=fd, rsi=buf, rdx=count` | bytes written or negative error |
| 3 | `close` | `rdi=fd` | 0 or negative error |
| 41 | `socket` | `rdi=AF_INET(2), rsi=SOCK_STREAM(1), rdx=0` | fd or negative error |
| 43 | `accept` | `rdi=listen_fd, rsi=NULL, rdx=NULL` | client fd or negative error |
| 49 | `bind` | `rdi=fd, rsi=sockaddr*, rdx=16` | 0 or negative error |
| 50 | `listen` | `rdi=fd, rsi=backlog` | 0 or negative error |
| 54 | `setsockopt` | `rdi=fd, rsi=level, rdx=optname, r10=optval*, r8=optlen` | 0 or negative error |
| 60 | `exit` | `rdi=status` | does not return |

The demo ignores errors. In a larger server, check for negative `rax`, especially from `socket`, `bind`, and `accept`.

## sockaddr_in

16 bytes on the stack:

```
offset 0   u16  sin_family  = AF_INET (2), host byte order
offset 2   u16  sin_port    = port in network (big-endian) byte order
offset 4   u32  sin_addr    = IPv4 address in network byte order
offset 8   u8[8] sin_zero   = zero
```

Network byte order values:

- Port 8080 = `0x1F90`; the two bytes are `1F 90`. Stored as a little-endian 16-bit word, that is `0x901F`.
- `127.0.0.1` = bytes `7F 00 00 01`; as a little-endian 32-bit word, `0x0100007F`.
- `0.0.0.0` = 0.

The 16-bit `sin_family` is stored before the port in memory. The builder computes:

```python
port_word = int.from_bytes(port.to_bytes(2, "big"), "little")
family_port = 2 | (port_word << 16)
# 8080 -> 0x901F0002
```

### 0.0.0.0 block (shortest)

```asm
push 0                      ; 8 bytes: sin_zero + zero addr
mov eax, 0x901F0002         ; family=2, port=8080
push rax                    ; 8 bytes: family+port + zero addr
mov rsi, rsp                ; sockaddr*
```

This yields `02 00 1F 90 00 00 00 00` followed by 8 zeros. Good.

### 127.0.0.1 block

In 64-bit mode `push imm32` pushes **8 bytes** (sign-extended). A naive push of the address would land at offset 8, not offset 4. The builder uses a 4-byte `sub rsp` + `mov`:

```asm
push 0                      ; 8 bytes sin_zero
mov eax, 0x0100007F
push rax                    ; 8 bytes: addr (4) + zeros (4)
mov eax, 0x901F0002
sub rsp, 4                  ; make room for family+port
mov [rsp], eax
mov rsi, rsp
```

Stack at `rsp`: `02 00 1F 90` (family+port), `7F 00 00 01` (addr), then 8 zero bytes. Correct.

## Response

The server writes one response per accepted connection:

```
HTTP/1.0 200 OK\r\n
Content-Type: text/html\r\n
Content-Encoding: br\r\n
\r\n
<Brotli stream>
```

- No `Content-Length`: HTTP/1.0 + close delimits the body.
- No `Connection: close`: HTTP/1.0 defaults to close.
- The browser decompresses because of `Content-Encoding`.
- The server writes **before** closing, then loops.

`write` can return fewer bytes than requested. With a few KB to a local socket this is unlikely; a robust server would loop until all bytes are written or use `sendfile`.

## Why read before write?

The demo reads up to 1 KB into the stack before replying. This drains the client's request from the receive buffer. Without it, `close()` on a socket with unread data can send RST instead of FIN, and a client may discard a response it has not read yet.

The read blocks until the client sends data. A port scanner that connects and sends nothing will hold that connection until it times out; that is acceptable for a local demo. `--no-drain` removes the read and saves 15 bytes; use it only if you understand the trade-off.

The read buffer is `rsp` itself. At that point the stack holds the sockaddr, which is no longer needed; overwriting it is safe. The listening fd is pushed only after the read.

## The response pointer

The builder places the response data before the server code and starts with:

```asm
call server      ; pushes the address after the call = data start
; data...
; code at the call target...
server:
pop r12          ; r12 = data start
```

No absolute address or relocation is needed. This is the same trick the original `qr-server` uses, arranged so the data is before the code (which saves the extra jump and lets the code sit at the end of the file).

## Building with NASM instead of Keystone

The template is NASM-compatible except for two details:

- Keystone does not accept `;` comments; the builder strips them before assembly. NASM does accept them.
- Keystone chooses short/near jumps automatically; do not write `jmp short` in the Keystone path. NASM accepts both.

A NASM build of a similar server:

```bash
brotli --best -f -o index.html.br index.html
nasm -f elf64 -O9 server.asm -o server.o
ld -N -s --build-id=none -z norelro server.o -o server
strip -s -R .comment -R .note.gnu.build-id -R .note.gnu.property server
qrencode -8 -l L -v 40 -s 8 -o server.png < server
```

`qrencode -8` uses 8-bit byte mode, which preserves nulls and the ELF header. A text-mode QR can corrupt binary data.

## Emulation with Unicorn

`scripts/asmweb.py emulate` maps each `PT_LOAD` at its virtual address, sets up a stack, and hooks `UC_X86_INS_SYSCALL`.

- On Unicorn 2.1.4, the syscall hook is called with `rip` at the `syscall` instruction and Unicorn advances past it automatically. Do **not** add 2 to `rip` in the hook, or you skip an instruction. Older Unicorn versions differ; the project pins `unicorn>=2.1`.
- The hook simulates `socket` → 3, `bind` → 0, `listen` → 0, `accept` → 4, `read` → the fake request, `write` → captured bytes, `close` → stop.
- It verifies the `sockaddr_in` family/port/address, then decompresses the captured body and compares it with the page.

This is not a security sandbox for arbitrary code, but it is a safe way to run the generated server without Linux.
