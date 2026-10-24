#!/usr/bin/env python3
"""asm-web: a hand-written x86_64 HTTP server in one QR code.

The page is Brotli-compressed, embedded in a tiny static ELF with a syscall-only
server, rendered as a version 40-L QR code, and can be verified without Linux
by emulating the ELF with Unicorn.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CAPACITY = 2953
BASE = 0x400000
EHDR_SIZE = 64
PHDR_SIZE = 56
ENTRY_OFF = 0x78
CALL_SIZE = 5
DATA_OFF = ENTRY_OFF + CALL_SIZE
VERSION = "1.0.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
ROOT = Path(__file__).resolve().parent.parent
SERVER_ASM = ROOT / "assets" / "server.asm"

SYSCALLS = {
    0: "read",
    1: "write",
    3: "close",
    41: "socket",
    43: "accept",
    49: "bind",
    50: "listen",
    54: "setsockopt",
    60: "exit",
}


class BuildError(Exception):
    pass


class Overflow(BuildError):
    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.details = details


class EmulationError(Exception):
    pass


@dataclass
class Built:
    page: bytes
    payload: bytes
    headers: bytes
    data: bytes
    code: bytes
    asm: str
    elf: bytes
    host: str
    port: int
    codec: str
    minified: bool
    length_header: bool
    drain: bool

    @property
    def headroom(self) -> int:
        return CAPACITY - len(self.elf)


def _enable_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def fail(message: str, code: int = 1) -> None:
    sys.stdout.flush()
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "?"


def require(module: str, package: str, why: str = ""):
    try:
        return __import__(module)
    except ImportError:
        suffix = f" ({why})" if why else ""
        fail(f"{package} is not installed{suffix}; run: python -m pip install {package}")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Page handling


PROTECTED = re.compile(
    r"<(pre|textarea|script|style|svg)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
TAG = re.compile(r"<(?:[^>\"']|\"[^\"]*\"|'[^']*')+>", re.DOTALL)


def _minify_plain(text: str) -> str:
    text = re.sub(r"<!--(?!\[if).*?-->", "", text, flags=re.DOTALL)
    pieces: list[str] = []
    pos = 0
    for match in TAG.finditer(text):
        pieces.append(re.sub(r"\s+", " ", text[pos : match.start()]))
        pieces.append(match.group(0))
        pos = match.end()
    pieces.append(re.sub(r"\s+", " ", text[pos:]))
    return "".join(pieces)


def minify_html(text: str) -> str:
    pieces: list[str] = []
    pos = 0
    for match in PROTECTED.finditer(text):
        pieces.append(_minify_plain(text[pos : match.start()]))
        pieces.append(match.group(0))
        pos = match.end()
    pieces.append(_minify_plain(text[pos:]))
    return "".join(pieces).strip()


def prepare_page(raw: bytes, do_minify: bool) -> tuple[bytes, bool]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"the page must be UTF-8 ({exc}); re-save it as UTF-8")
    if not do_minify:
        return raw, False
    return minify_html(text).encode("utf-8"), True


def external_refs(page: bytes) -> list[str]:
    text = page.decode("utf-8", "replace")
    checks = [
        (r"<script[^>]+src\s*=", "<script src>"),
        (r"<link[^>]+href\s*=", "<link href>"),
        (r"<img[^>]+src\s*=", "<img src>"),
        (r"<iframe[^>]+src\s*=", "<iframe src>"),
        (r"@import\b", "CSS @import"),
        (r"url\(\s*['\"]?https?:", "CSS url(http...)"),
        (r"\bfetch\s*\(", "fetch()"),
        (r"XMLHttpRequest", "XMLHttpRequest"),
    ]
    found: list[str] = []
    for pattern, label in checks:
        if re.search(pattern, text, re.IGNORECASE):
            found.append(label)
    return found


# ---------------------------------------------------------------------------
# Compression and HTTP response


def compress_bytes(data: bytes, codec: str) -> bytes:
    if codec == "gzip":
        return gzip.compress(data, compresslevel=9, mtime=0)
    brotli = require("brotli", "brotli", "Brotli compression")
    streams = []
    for mode in (brotli.MODE_TEXT, brotli.MODE_GENERIC):
        for lgwin in (22, 24):
            streams.append(
                brotli.compress(data, mode=mode, quality=11, lgwin=lgwin)
            )
    return min(streams, key=len)


def decompress_payload(payload: bytes, encoding: str | None = None) -> tuple[bytes, str]:
    if encoding:
        encoding = encoding.lower().strip()
        if "br" in encoding:
            return _decompress_brotli(payload), "brotli"
        if "gzip" in encoding:
            return _decompress_gzip(payload), "gzip"
    if payload[:2] == b"\x1f\x8b":
        return _decompress_gzip(payload), "gzip"
    return _decompress_brotli(payload), "brotli"


def _decompress_gzip(body: bytes) -> bytes:
    try:
        obj = zlib.decompressobj(16 + zlib.MAX_WBITS)
        return obj.decompress(body) + obj.flush()
    except zlib.error as exc:
        raise ValueError(f"not a gzip stream ({exc})") from exc


def _decompress_brotli(body: bytes) -> bytes:
    brotli = require("brotli", "brotli", "Brotli decompression")
    try:
        dec = brotli.Decompressor()
        out = bytearray()
        # The ELF may carry code after the Brotli stream. Feed one byte at a
        # time so we stop exactly at the stream end instead of handing the
        # trailing machine code to the decoder.
        for byte in body:
            out += dec.process(bytes((byte,)))
            if dec.is_finished():
                return bytes(out)
        return brotli.decompress(body)
    except Exception as exc:
        raise ValueError(f"not a Brotli stream ({exc})") from exc


def make_headers(codec: str, payload_len: int, include_length: bool) -> bytes:
    encoding = "br" if codec == "brotli" else "gzip"
    lines = [
        "HTTP/1.0 200 OK",
        "Content-Type: text/html",
        f"Content-Encoding: {encoding}",
    ]
    if include_length:
        lines.append(f"Content-Length: {payload_len}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


# ---------------------------------------------------------------------------
# ELF and assembly


def family_port(port: int) -> int:
    network = int.from_bytes(port.to_bytes(2, "big"), "little")
    return 2 | (network << 16)


def bind_block(host: str, port: int) -> str:
    fp = family_port(port)
    if host == "0.0.0.0":
        return (
            "push 0\n"
            f"mov eax, 0x{fp:08X}\n"
            "push rax\n"
            "mov rsi, rsp\n"
        )
    if host == "127.0.0.1":
        return (
            "push 0\n"
            "mov eax, 0x0100007F\n"
            "push rax\n"
            f"mov eax, 0x{fp:08X}\n"
            "sub rsp, 4\n"
            "mov [rsp], eax\n"
            "mov rsi, rsp\n"
        )
    raise BuildError(f"unsupported host: {host}")


def drain_block(drain: bool) -> str:
    if not drain:
        return "; request not drained (--no-drain)"
    return (
        "xor eax, eax\n"
        "mov edi, r8d\n"
        "mov rsi, rsp\n"
        "mov edx, 1024\n"
        "syscall\n"
    )


def assemble_server(response_len: int, host: str, port: int, drain: bool) -> tuple[bytes, str]:
    if not SERVER_ASM.is_file():
        raise BuildError(f"assembly template not found: {SERVER_ASM}")
    source = SERVER_ASM.read_text(encoding="utf-8")
    source = source.replace("{{BIND}}", bind_block(host, port))
    source = source.replace("{{DRAIN}}", drain_block(drain))
    source = source.replace("{{RESPONSE_LEN}}", str(response_len))

    # Keystone's Intel parser does not accept NASM-style ';' comments.
    assembly_source = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith(";")
    )
    keystone = require("keystone", "keystone-engine", "x86_64 assembly")
    ks = keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_64)
    try:
        encoding, count = ks.asm(assembly_source)
    except Exception as exc:
        raise BuildError(f"assembly failed: {exc}") from exc
    if encoding is None:
        raise BuildError("assembly produced no bytes")
    return bytes(encoding), source


def make_elf(page: bytes, payload: bytes, headers: bytes, code: bytes) -> bytes:
    data = headers + payload
    total = DATA_OFF + len(data) + len(code)
    e_ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    ehdr = struct.pack(
        "<16sHHIQQQIHHHHHH",
        e_ident,
        2,          # ET_EXEC
        0x3E,       # x86_64
        1,
        BASE + ENTRY_OFF,
        0x40,
        0,
        0,
        64,
        56,
        1,
        64,
        0,
        0,
    )
    phdr = struct.pack(
        "<IIQQQQQQ",
        1,          # PT_LOAD
        7,          # RWE
        0,
        BASE,
        BASE,
        total,
        total,
        0x1000,
    )
    call = b"\xe8" + struct.pack("<i", len(data))
    elf = ehdr + phdr + call + data + code
    assert len(elf) == total, (len(elf), total)
    return elf


def build_page(
    raw_page: bytes,
    *,
    codec: str = "brotli",
    minify: bool = False,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    include_length: bool = False,
    drain: bool = True,
) -> Built:
    page, was_minified = prepare_page(raw_page, minify)
    payload = compress_bytes(page, codec)
    headers = make_headers(codec, len(payload), include_length)
    data = headers + payload
    code, asm = assemble_server(len(data), host, port, drain)
    total = DATA_OFF + len(data) + len(code)
    if total > CAPACITY:
        details = {
            "page": len(page),
            "payload": len(payload),
            "headers": len(headers),
            "code": len(code),
            "elf": total,
            "over": total - CAPACITY,
            "codec": codec,
        }
        raise Overflow(
            f"ELF is {total:,} B, {total - CAPACITY:,} B over the "
            f"{CAPACITY:,} B QR v40-L limit",
            details,
        )
    elf = make_elf(page, payload, headers, code)
    return Built(
        page=page,
        payload=payload,
        headers=headers,
        data=data,
        code=code,
        asm=asm,
        elf=elf,
        host=host,
        port=port,
        codec=codec,
        minified=was_minified,
        length_header=include_length,
        drain=drain,
    )


# ---------------------------------------------------------------------------
# ELF parsing, response extraction, inspection


def parse_elf(data: bytes) -> dict:
    if len(data) < EHDR_SIZE or data[:4] != b"\x7fELF":
        raise BuildError("not an ELF file")
    if data[4] != 2 or data[5] != 1:
        raise BuildError("only little-endian 64-bit ELF is supported")
    (
        e_type,
        e_machine,
        e_version,
        e_entry,
        e_phoff,
        e_shoff,
        e_flags,
        e_ehsize,
        e_phentsize,
        e_phnum,
        e_shentsize,
        e_shnum,
        e_shstrndx,
    ) = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
    segments = []
    for i in range(e_phnum):
        offset = e_phoff + i * e_phentsize
        if offset + PHDR_SIZE > len(data):
            break
        (
            p_type,
            p_flags,
            p_offset,
            p_vaddr,
            p_paddr,
            p_filesz,
            p_memsz,
            p_align,
        ) = struct.unpack_from("<IIQQQQQQ", data, offset)
        segments.append(
            {
                "type": p_type,
                "flags": p_flags,
                "offset": p_offset,
                "vaddr": p_vaddr,
                "paddr": p_paddr,
                "filesz": p_filesz,
                "memsz": p_memsz,
                "align": p_align,
            }
        )
    return {
        "type": e_type,
        "machine": e_machine,
        "entry": e_entry,
        "phoff": e_phoff,
        "phnum": e_phnum,
        "shoff": e_shoff,
        "shnum": e_shnum,
        "segments": segments,
    }


def find_response(elf: bytes) -> dict:
    starts = []
    for magic in (b"HTTP/1.0 200 OK\r\n", b"HTTP/1.1 200 OK\r\n"):
        pos = elf.find(magic)
        if pos >= 0:
            starts.append((pos, magic))
    if not starts:
        raise BuildError("no HTTP response headers found in the ELF")
    pos, _ = min(starts)
    end = elf.find(b"\r\n\r\n", pos)
    if end < 0:
        raise BuildError("HTTP headers are not terminated by CRLFCRLF")
    end += 4
    raw_headers = elf[pos:end]
    text = raw_headers.decode("latin-1", "replace")
    headers: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return {
        "header_start": pos,
        "header_end": end,
        "headers": headers,
        "raw_headers": raw_headers,
        "body": elf[end:],
        "encoding": headers.get("content-encoding"),
    }


def extract_page(elf: bytes) -> tuple[bytes, dict]:
    response = find_response(elf)
    body = response["body"]
    length = response["headers"].get("content-length")
    if length:
        try:
            body = body[: int(length)]
        except ValueError:
            pass
    page, codec = decompress_payload(body, response["encoding"])
    return page, {
        "headers": response["headers"],
        "header_bytes": len(response["raw_headers"]),
        "payload_bytes": len(body),
        "codec": codec,
    }


def _file_offset(parsed: dict, vaddr: int) -> int | None:
    for seg in parsed["segments"]:
        if seg["type"] != 1:
            continue
        if seg["vaddr"] <= vaddr < seg["vaddr"] + seg["filesz"]:
            return seg["offset"] + (vaddr - seg["vaddr"])
    return None


def disassemble_elf(elf: bytes) -> dict:
    capstone = require("capstone", "capstone", "disassembly")
    parsed = parse_elf(elf)
    entry = parsed["entry"]
    entry_off = _file_offset(parsed, entry)
    if entry_off is None:
        raise BuildError("entry point is not inside a loadable segment")
    response = find_response(elf)
    header_off = response["header_start"]

    if elf[entry_off] == 0xE8 and entry_off + 5 <= len(elf):
        # asm-web layout: call over the data, then the server code.
        rel = struct.unpack_from("<i", elf, entry_off + 1)[0]
        target = entry + 5 + rel
        target_off = _file_offset(parsed, target)
        if target_off is None:
            raise BuildError("call target is not inside a loadable segment")
        start = target_off
        end = len(elf)
        mode = "call-over-data"
    elif entry_off < header_off and header_off - entry_off < 600:
        # Reference layout: server code, call, then the response data.
        start = entry_off
        end = header_off
        mode = "entry-to-data"
    else:
        start = entry_off
        end = len(elf)
        mode = "entry"

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    insns = list(md.disasm(elf[start:end], entry + (start - entry_off)))
    syscalls: list[str] = []
    pending_push = None
    rax = None
    for insn in insns:
        if insn.mnemonic == "push":
            token = insn.op_str.strip()
            if token.startswith("0x") or token.isdigit():
                try:
                    pending_push = int(token, 0)
                except ValueError:
                    pending_push = None
            else:
                pending_push = None
        elif insn.mnemonic == "pop" and insn.op_str == "rax":
            if pending_push is not None:
                rax = pending_push
        elif insn.mnemonic == "xor" and insn.op_str.replace(" ", "") == "eax,eax":
            rax = 0
        elif insn.mnemonic == "mov" and insn.op_str.startswith("eax, 0x"):
            try:
                rax = int(insn.op_str.split(",", 1)[1].strip(), 16)
            except ValueError:
                pass
        elif insn.mnemonic == "syscall":
            syscalls.append(SYSCALLS.get(rax, f"syscall {rax}"))
    return {
        "mode": mode,
        "start": start,
        "end": end,
        "count": len(insns),
        "insns": insns,
        "syscalls": syscalls,
    }


def inspect_data(elf: bytes, html_out: Path | None = None, show_disasm: bool = True) -> None:
    parsed = parse_elf(elf)
    print(f"  ELF       ET_EXEC x86_64, entry 0x{parsed['entry']:x}, "
          f"{len(parsed['segments'])} segment(s)")
    for seg in parsed["segments"]:
        if seg["type"] == 1:
            flags = (
                ("R" if seg["flags"] & 4 else "-")
                + ("W" if seg["flags"] & 2 else "-")
                + ("X" if seg["flags"] & 1 else "-")
            )
            print(
                f"  LOAD      off 0x{seg['offset']:x} vaddr 0x{seg['vaddr']:x} "
                f"filesz 0x{seg['filesz']:x} flags {flags}"
            )

    try:
        response = find_response(elf)
        print(
            f"  response  {response['headers'].get('content-encoding', '?')} "
            f"body, {len(response['raw_headers'])} B headers"
        )
        page, meta = extract_page(elf)
        print(
            f"  page      {len(page):,} B, {meta['codec']}, "
            f"sha256 {digest(page)[:16]}..."
        )
        if html_out:
            html_out.parent.mkdir(parents=True, exist_ok=True)
            html_out.write_bytes(page)
            print(f"  html      -> {html_out}")
    except Exception as exc:
        print(f"  page      could not be extracted: {exc}")

    if show_disasm:
        try:
            dis = disassemble_elf(elf)
            print(
                f"  code      {dis['count']} insns ({dis['mode']}), "
                f"syscalls: {', '.join(dis['syscalls']) or 'none'}"
            )
            for insn in dis["insns"]:
                print(
                    f"    0x{insn.address:08x}  {insn.bytes.hex():<16} "
                    f"{insn.mnemonic:<7} {insn.op_str}"
                )
        except SystemExit:
            raise
        except Exception as exc:
            print(f"  code      disassembly unavailable: {exc}")


# ---------------------------------------------------------------------------
# Unicorn emulation


def emulate_elf(
    elf: bytes,
    expected_page: bytes | None = None,
    expected_port: int | None = None,
    connections: int = 2,
) -> dict:
    unicorn = require("unicorn", "unicorn", "ELF emulation")
    from unicorn.x86_const import (  # type: ignore
        UC_X86_INS_SYSCALL,
        UC_X86_REG_RAX,
        UC_X86_REG_RDI,
        UC_X86_REG_RDX,
        UC_X86_REG_RIP,
        UC_X86_REG_RSI,
        UC_X86_REG_RSP,
    )

    parsed = parse_elf(elf)
    mu = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
    mapped = False
    for seg in parsed["segments"]:
        if seg["type"] != 1:
            continue
        start = seg["vaddr"] & ~0xFFF
        end = (seg["vaddr"] + seg["memsz"] + 0xFFF) & ~0xFFF
        try:
            mu.mem_map(start, max(end - start, 0x1000))
        except unicorn.UcError:
            pass
        mu.mem_write(seg["vaddr"], elf[seg["offset"] : seg["offset"] + seg["filesz"]])
        mapped = True
    if not mapped:
        raise EmulationError("ELF has no loadable segment")

    stack = 0x70000000
    mu.mem_map(stack, 0x100000)
    mu.reg_write(UC_X86_REG_RSP, stack + 0x80000)
    mu.reg_write(UC_X86_REG_RIP, parsed["entry"])

    request = (
        b"GET / HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Accept-Encoding: br, gzip\r\n"
        b"Connection: close\r\n\r\n"
    )
    state = {
        "sockaddr": None,
        "writes": [],
        "responses": [],
        "client_fd": 4,
        "accepted": 0,
        "closed": 0,
        "connections": max(1, connections),
        "error": None,
        "unknown": [],
    }

    def hook(uc, _user):
        rax = uc.reg_read(UC_X86_REG_RAX)
        rdi = uc.reg_read(UC_X86_REG_RDI)
        rsi = uc.reg_read(UC_X86_REG_RSI)
        rdx = uc.reg_read(UC_X86_REG_RDX)
        try:
            if rax == 41:  # socket
                uc.reg_write(UC_X86_REG_RAX, 3)
            elif rax == 49:  # bind
                state["sockaddr"] = bytes(uc.mem_read(rsi, 16))
                uc.reg_write(UC_X86_REG_RAX, 0)
            elif rax == 50:  # listen
                uc.reg_write(UC_X86_REG_RAX, 0)
            elif rax == 43:  # accept
                if state["accepted"] >= state["connections"]:
                    state["error"] = "server accepted too many connections"
                    uc.emu_stop()
                else:
                    state["accepted"] += 1
                    state["client_fd"] = 3 + state["accepted"]
                    uc.reg_write(UC_X86_REG_RAX, state["client_fd"])
            elif rax == 0:  # read
                if rdi == state["client_fd"]:
                    data = request[:rdx]
                    uc.mem_write(rsi, data)
                    uc.reg_write(UC_X86_REG_RAX, len(data))
                else:
                    uc.reg_write(UC_X86_REG_RAX, 0)
            elif rax == 1:  # write
                state["writes"].append(bytes(uc.mem_read(rsi, rdx)))
                uc.reg_write(UC_X86_REG_RAX, rdx)
            elif rax == 3:  # close
                state["responses"].append(b"".join(state["writes"]))
                state["writes"].clear()
                state["closed"] += 1
                uc.reg_write(UC_X86_REG_RAX, 0)
                if state["closed"] >= state["connections"]:
                    uc.emu_stop()
            elif rax == 54:  # setsockopt
                uc.reg_write(UC_X86_REG_RAX, 0)
            elif rax == 60:  # exit
                uc.emu_stop()
            else:
                state["unknown"].append(rax)
                state["error"] = f"unexpected syscall {rax}"
                uc.emu_stop()
        except unicorn.UcError as exc:
            state["error"] = f"emulation memory error at syscall {rax}: {exc}"
            uc.emu_stop()

    mu.hook_add(unicorn.UC_HOOK_INSN, hook, None, 1, 0, UC_X86_INS_SYSCALL)
    try:
        mu.emu_start(parsed["entry"], 0, count=500000)
    except unicorn.UcError as exc:
        raise EmulationError(f"emulation failed: {exc}") from exc

    if state["error"]:
        raise EmulationError(state["error"])
    if state["closed"] < state["connections"]:
        raise EmulationError(
            f"server closed {state['closed']} of {state['connections']} connections"
        )
    if not state["responses"]:
        raise EmulationError("server wrote no response")

    sockaddr = state["sockaddr"]
    if not sockaddr:
        raise EmulationError("server did not call bind")
    family = int.from_bytes(sockaddr[0:2], "little")
    port = int.from_bytes(sockaddr[2:4], "big")
    host = ".".join(str(b) for b in sockaddr[4:8])
    if family != 2:
        raise EmulationError(f"bind family is {family}, expected AF_INET (2)")
    if expected_port is not None and port != expected_port:
        raise EmulationError(f"bind port is {port}, expected {expected_port}")
    host_label = "0.0.0.0" if host == "0.0.0.0" else host

    status = ""
    headers: dict[str, str] = {}
    page = b""
    codec = ""
    for index, response in enumerate(state["responses"], 1):
        head, sep, body = response.partition(b"\r\n\r\n")
        if not sep:
            raise EmulationError(
                f"connection {index}: response has no CRLFCRLF terminator"
            )
        text = head.decode("latin-1", "replace")
        status = text.split("\r\n", 1)[0]
        if not status.startswith("HTTP/"):
            raise EmulationError(
                f"connection {index}: unexpected status line {status!r}"
            )
        headers = {}
        for line in text.split("\r\n")[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        page, codec = decompress_payload(body, headers.get("content-encoding"))
        if expected_page is not None and page != expected_page:
            raise EmulationError(
                f"connection {index}: served page does not match: "
                f"{len(page):,} B vs {len(expected_page):,} B"
            )
    return {
        "connections": state["connections"],
        "accepted": state["accepted"],
        "port": port,
        "host": host_label,
        "status": status,
        "headers": headers,
        "codec": codec,
        "page_bytes": len(page),
        "response_bytes": len(state["responses"][-1]),
        "syscalls": state["unknown"],
    }


# ---------------------------------------------------------------------------
# QR


def make_qr(elf: bytes, force_v40: bool):
    segno = require("segno", "segno", "QR generation")
    options = {"error": "l", "boost_error": False}
    if force_v40:
        options["version"] = 40
    try:
        return segno.make_qr(elf, **options)
    except Exception as exc:
        raise BuildError(f"QR generation failed: {exc}") from exc


def read_qr_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise BuildError(f"QR image not found: {path}")
    zxingcpp = require("zxingcpp", "zxing-cpp", "QR decoding")
    from PIL import Image  # type: ignore

    with Image.open(path) as image:
        results = zxingcpp.read_barcodes(image)
    results = [r for r in results if r.valid]
    if not results:
        raise BuildError(f"no readable QR code found in {path}")
    return bytes(results[0].bytes or b"")


# ---------------------------------------------------------------------------
# Commands


def _print_overflow(exc: Overflow, host: str) -> None:
    d = exc.details
    print(f"error: {exc}", file=sys.stderr)
    print(
        f"  page      {d['page']:,} B\n"
        f"  payload   {d['payload']:,} B ({d['codec']})\n"
        f"  headers   {d['headers']:,} B\n"
        f"  asm code  {d['code']:,} B\n"
        f"  ELF       {d['elf']:,} B",
        file=sys.stderr,
    )
    print("  try:", file=sys.stderr)
    print("    shrink the page (see references/animation-playbook.md)", file=sys.stderr)
    print("    python scripts/asmweb.py size PAGE --minify", file=sys.stderr)
    print("    build --no-drain        (saves 15 B)", file=sys.stderr)
    if host != "0.0.0.0":
        print("    build --host 0.0.0.0    (saves ~12 B, exposes the LAN)", file=sys.stderr)
    print("    drop Content-Length if you added --length", file=sys.stderr)


def cmd_size(args) -> int:
    raw = Path(args.page).read_bytes()
    variants: list[tuple[str, bytes]] = [("as-is", raw)]
    minified = None
    try:
        minified, _ = prepare_page(raw, True)
    except BuildError as exc:
        print(f"warning: {exc}", file=sys.stderr)
    if minified is not None and minified != raw:
        variants.append(("minified", minified))

    print(f"asm-web size  {args.page}")
    print(f"  {'variant':<10} {'HTML':>10} {'brotli q11':>12} {'gzip -9':>10}")
    sizes: dict[tuple[str, str], int] = {}
    for label, page in variants:
        br = len(compress_bytes(page, "brotli"))
        gz = len(compress_bytes(page, "gzip"))
        sizes[(label, "brotli")] = br
        sizes[(label, "gzip")] = gz
        print(f"  {label:<10} {len(page):>9,} B {br:>10,} B {gz:>8,} B")

    preferred = "minified" if args.minify else "as-is"
    candidates = [
        item
        for item in sizes.items()
        if item[0][1] == args.codec and item[0][0] == preferred
    ]
    if not candidates:
        candidates = [item for item in sizes.items() if item[0][1] == args.codec]
    (label, codec), payload_len = min(candidates, key=lambda kv: kv[1])
    page = dict(variants)[label]
    headers = make_headers(codec, payload_len, args.length)
    code, _ = assemble_server(len(headers) + payload_len, args.host, args.port, not args.no_drain)
    total = DATA_OFF + len(headers) + payload_len + len(code)
    fit = CAPACITY - total
    print()
    print(f"  chosen    {label} / {codec}")
    print(f"  layout    {EHDR_SIZE + PHDR_SIZE} B ELF hdr + {CALL_SIZE} B call + "
          f"{len(headers)} B HTTP + {payload_len:,} B payload + {len(code)} B asm")
    print(f"  ELF       {total:,} B / {CAPACITY:,} B  "
          f"({'fits, ' + format(fit, ',') + ' B headroom' if fit >= 0 else 'over by ' + format(-fit, ',') + ' B'})")
    if fit >= 0:
        qr = make_qr(b"\0" * total, False)
        print(f"  QR        version {qr.version}-{str(qr.error).upper()} for this size")

    refs = external_refs(raw)
    if refs:
        print(f"  warning   external references: {', '.join(refs)}")
        print("            the page will not work offline; inline or remove them")
    return 0 if fit >= 0 else 2


def cmd_build(args) -> int:
    if not 1 <= args.scale <= 40:
        fail("--scale must be between 1 and 40")
    if not 0 <= args.border <= 32:
        fail("--border must be between 0 and 32")
    if not 1 <= args.port <= 65535:
        fail("--port must be between 1 and 65535")
    if args.host == "0.0.0.0":
        print("warning: --host 0.0.0.0 exposes the page to your LAN", file=sys.stderr)
    source = Path(args.page)
    if not source.is_file():
        fail(f"page not found: {source}")
    raw = source.read_bytes()
    try:
        built = build_page(
            raw,
            codec=args.codec,
            minify=args.minify,
            host=args.host,
            port=args.port,
            include_length=args.length,
            drain=not args.no_drain,
        )
    except Overflow as exc:
        _print_overflow(exc, args.host)
        return 2
    except BuildError as exc:
        fail(str(exc))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", args.name or source.stem).strip("-._") or "server"
    elf_path = out_dir / f"{name}.elf"
    page_path = out_dir / f"{name}.page.html"
    png_path = out_dir / f"{name}.png"
    svg_path = out_dir / f"{name}.svg"
    manifest_path = out_dir / f"{name}.json"

    elf_path.write_bytes(built.elf)
    page_path.write_bytes(built.page)
    try:
        qr = make_qr(built.elf, args.v40)
    except BuildError as exc:
        fail(str(exc))
    qr.save(png_path, scale=args.scale, border=args.border)
    qr.save(svg_path, scale=args.scale, border=args.border)
    modules = int(qr.symbol_size(scale=1, border=0)[0])
    png_px = int(qr.symbol_size(scale=args.scale, border=args.border)[0])

    page_out, meta = extract_page(built.elf)
    if page_out != built.page:
        fail("internal check failed: embedded page does not match the source")
    if module_available("zxingcpp") and module_available("PIL"):
        decoded = read_qr_bytes(png_path)
        if decoded != built.elf:
            fail("internal check failed: QR does not contain the ELF")
    else:
        print("warning: zxing-cpp/Pillow missing; skipped QR decode check", file=sys.stderr)

    manifest = {
        "tool": f"asm-web {VERSION}",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(source),
        "page_bytes": len(built.page),
        "page_sha256": digest(built.page),
        "minified": built.minified,
        "codec": built.codec,
        "payload_bytes": len(built.payload),
        "payload_sha256": digest(built.payload),
        "http_headers_bytes": len(built.headers),
        "asm_bytes": len(built.code),
        "elf_bytes": len(built.elf),
        "elf_sha256": digest(built.elf),
        "capacity_bytes": CAPACITY,
        "headroom_bytes": built.headroom,
        "host": built.host,
        "port": built.port,
        "drain": built.drain,
        "length_header": built.length_header,
        "layout": {
            "elf_header": EHDR_SIZE,
            "program_header": PHDR_SIZE,
            "call": CALL_SIZE,
            "http_headers": len(built.headers),
            "payload": len(built.payload),
            "asm": len(built.code),
        },
        "qr": {
            "version": qr.version,
            "error": str(qr.error).upper(),
            "modules": modules,
            "png_px": png_px,
        },
        "artifacts": {
            "elf": str(elf_path),
            "page": str(page_path),
            "png": str(png_path),
            "svg": str(svg_path),
        },
    }
    if args.emulate:
        try:
            emu = emulate_elf(built.elf, expected_page=built.page, expected_port=built.port)
            manifest["emulation"] = emu
        except EmulationError as exc:
            fail(f"emulation check failed: {exc}")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    used = len(built.elf) / CAPACITY * 100
    print("asm-web build")
    print(f"  source    {source.name:<30} {len(built.page):>9,} B")
    print(f"  payload   {len(built.payload):>9,} B  {built.codec} q11")
    print(
        f"  ELF       {len(built.elf):>9,} B  "
        f"(hdr {EHDR_SIZE + PHDR_SIZE} + call {CALL_SIZE} + http "
        f"{len(built.headers)} + payload {len(built.payload):,} + asm {len(built.code)})"
    )
    print(
        f"  budget    {built.headroom:>9,} B headroom  "
        f"({used:.0f}% of {CAPACITY:,} B)"
    )
    print(
        f"  QR        version {qr.version}-{str(qr.error).upper()}, "
        f"{modules}x{modules} modules, {png_px}x{png_px} px"
    )
    for path in (elf_path, page_path, png_path, svg_path, manifest_path):
        print(f"  file      {path}")
    refs = external_refs(built.page)
    if refs:
        print(f"  warning   external references: {', '.join(refs)}")
    if args.emulate:
        print(
            f"  emulate   PASS  {manifest['emulation']['connections']} connections, "
            f"{manifest['emulation']['status']}  "
            f"{manifest['emulation']['response_bytes']:,} B response, "
            f"{manifest['emulation']['page_bytes']:,} B page"
        )
    return 0


def cmd_extract(args) -> int:
    qr_path = Path(args.image)
    elf = read_qr_bytes(qr_path)
    if elf[:4] != b"\x7fELF":
        out = Path(args.out) if args.out else qr_path.with_suffix(".bin")
        out.write_bytes(elf)
        fail(f"QR payload is not an ELF; raw bytes saved to {out}")
    out = Path(args.out) if args.out else qr_path.with_suffix(".elf")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(elf)
    print(f"extracted  {qr_path} -> {out}")
    print(f"  size     {len(elf):,} B")
    print(f"  sha256   {digest(elf)}")
    print("  note     extracted only; nothing was executed")
    if args.html:
        page, _ = extract_page(elf)
        html_path = Path(args.html)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_bytes(page)
        print(f"  html     {len(page):,} B -> {html_path}")
    if args.inspect:
        inspect_data(elf, show_disasm=not args.no_disasm)
    return 0


def cmd_inspect(args) -> int:
    path = Path(args.elf)
    if not path.is_file():
        fail(f"ELF not found: {path}")
    elf = path.read_bytes()
    print(f"inspect  {path}")
    print(f"  size     {len(elf):,} B")
    print(f"  sha256   {digest(elf)}")
    inspect_data(elf, Path(args.html) if args.html else None, not args.no_disasm)
    return 0


def cmd_emulate(args) -> int:
    path = Path(args.elf)
    if not path.is_file():
        fail(f"ELF not found: {path}")
    expected = None
    if args.page:
        page_path = Path(args.page)
        if not page_path.is_file():
            fail(f"page not found: {page_path}")
        expected = page_path.read_bytes()
    try:
        result = emulate_elf(path.read_bytes(), expected, args.port, args.connections)
    except EmulationError as exc:
        fail(f"emulation failed: {exc}")
    print("asm-web emulate")
    print(f"  bind      {result['host']}:{result['port']}")
    print(f"  requests  {result['connections']} connections")
    print(f"  response  {result['response_bytes']:,} B, {result['status']}")
    print(f"  page      {result['page_bytes']:,} B ({result['codec']})")
    print("  result    PASS")
    return 0


def cmd_doctor(args) -> int:
    print(f"asm-web {VERSION}")
    print(
        f"  python    {platform.python_version()} "
        f"({platform.system()} {platform.machine()})"
    )
    checks = [
        ("segno", "segno", "segno", True, "QR generation"),
        ("brotli", "brotli", "brotli", True, "page compression"),
        ("keystone", "keystone", "keystone-engine", True, "x86_64 assembly"),
        ("capstone", "capstone", "capstone", False, "ELF disassembly"),
        ("unicorn", "unicorn", "unicorn", False, "ELF emulation"),
        ("zxing-cpp", "zxingcpp", "zxing-cpp", False, "QR decoding"),
        ("Pillow", "PIL", "Pillow", False, "QR image loading"),
    ]
    missing_required = False
    missing_optional = False
    for label, module, package, required, why in checks:
        if module_available(module):
            print(f"  {label:<10} ok  {package_version(package)}  ({why})")
        else:
            print(f"  {label:<10} {'missing' if required else 'optional, missing'}  ({why})")
            if required:
                missing_required = True
            else:
                missing_optional = True
    print(f"  capacity  {CAPACITY:,} B  (QR v40-L, 177x177 modules)")
    print(f"  layout    {EHDR_SIZE + PHDR_SIZE} B headers + {CALL_SIZE} B call + "
          f"HTTP + payload + asm <= {CAPACITY:,} B")
    print("  hosts     127.0.0.1 (default) or 0.0.0.0")
    if missing_required:
        print("  install   python -m pip install segno brotli keystone-engine")
        return 1
    if missing_optional:
        print("  install   python -m pip install capstone unicorn zxing-cpp pillow")
    print("  note      Windows can build and emulate; run natively on Linux")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asmweb.py",
        description=(
            "Build a hand-written x86_64 HTTP server serving a self-contained "
            "animated page, then pack the ELF into a v40-L QR code."
        ),
    )
    parser.add_argument("--version", action="version", version=f"asm-web {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build ELF, QR, and manifest")
    build.add_argument("page")
    build.add_argument("-o", "--out", default="dist")
    build.add_argument("-n", "--name")
    build.add_argument("--codec", choices=("brotli", "gzip"), default="brotli")
    build.add_argument("--minify", action="store_true")
    build.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default=DEFAULT_HOST)
    build.add_argument("--port", type=int, default=DEFAULT_PORT)
    build.add_argument("--length", action="store_true", help="include Content-Length")
    build.add_argument("--no-drain", action="store_true", help="skip the request read (saves 15 B)")
    build.add_argument("--v40", action="store_true", help="force QR version 40")
    build.add_argument("--scale", type=int, default=8)
    build.add_argument("--border", type=int, default=4)
    build.add_argument("--emulate", action="store_true", help="verify with Unicorn")
    build.set_defaults(func=cmd_build)

    size = sub.add_parser("size", help="report page and ELF sizes against the budget")
    size.add_argument("page")
    size.add_argument("--minify", action="store_true")
    size.add_argument("--codec", choices=("brotli", "gzip"), default="brotli")
    size.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default=DEFAULT_HOST)
    size.add_argument("--port", type=int, default=DEFAULT_PORT)
    size.add_argument("--length", action="store_true")
    size.add_argument("--no-drain", action="store_true")
    size.set_defaults(func=cmd_size)

    extract = sub.add_parser("extract", help="decode a QR image into an ELF")
    extract.add_argument("image")
    extract.add_argument("-o", "--out")
    extract.add_argument("--html", help="also extract the embedded page")
    extract.add_argument("--inspect", action="store_true")
    extract.add_argument("--no-disasm", action="store_true")
    extract.set_defaults(func=cmd_extract)

    inspect = sub.add_parser("inspect", help="statically inspect an ELF (never executes)")
    inspect.add_argument("elf")
    inspect.add_argument("--html", help="extract the embedded page")
    inspect.add_argument("--no-disasm", action="store_true")
    inspect.set_defaults(func=cmd_inspect)

    emulate = sub.add_parser("emulate", help="emulate one request with Unicorn")
    emulate.add_argument("elf")
    emulate.add_argument("--page", help="expected page to compare against")
    emulate.add_argument("--port", type=int, help="expected bind port")
    emulate.add_argument("--connections", type=int, default=2, help="connections to serve before stopping (default: 2)")
    emulate.set_defaults(func=cmd_emulate)

    doctor = sub.add_parser("doctor", help="check dependencies and the budget")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv=None) -> int:
    _enable_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except BuildError as exc:
        fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
