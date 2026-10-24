; asm-web server body (x86_64 Linux, Intel syntax, no libc)
;
; The builder prepends a `call` to this code and appends the HTTP response
; data between the call and this code. The call's return address is therefore
; the start of that data; `pop r12` captures it. The code then serves one
; response per accepted connection and loops forever.
;
; Placeholders are substituted by scripts/asmweb.py before assembly.
;
; Build layout (see references/elf-layout.md):
;   [ELF header + program header : 120 B]
;   [call server                :   5 B]
;   [HTTP headers + payload     :  N B]  <- r12 points here
;   [this code                  :  ~109 B]
;
; The browser decompresses the body because the response carries
; `Content-Encoding: br` (or gzip), so this server needs no decoder.

pop r12

; socket(AF_INET=2, SOCK_STREAM=1, 0)
push 41
pop rax
push 2
pop rdi
push 1
pop rsi
xor edx, edx
syscall
xchg edi, eax

; sockaddr_in for the requested host and port
{{BIND}}
push 16
pop rdx

; bind(fd, sockaddr, 16)
push 49
pop rax
syscall

; listen(fd, 0)
push 50
pop rax
xor esi, esi
syscall

accept_loop:
; accept(fd, NULL, NULL)
push 43
pop rax
xor esi, esi
xor edx, edx
syscall
mov r8, rax

; Keep the listening fd in r9: the read below clobbers rdi, and the server
; must accept the next connection after closing this one.
mov r9, rdi

; Read the request before replying. This drains the receive buffer so that
; close() sends FIN instead of RST; without it, a client can lose the response.
; Use build --no-drain to save 15 bytes in an emergency.
{{DRAIN}}

; write(client, response, {{RESPONSE_LEN}})
mov rsi, r12
push 1
pop rax
mov edi, r8d
mov edx, {{RESPONSE_LEN}}
syscall

; close(client)
push 3
pop rax
mov edi, r8d
syscall

mov rdi, r9
jmp accept_loop
