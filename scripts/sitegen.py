"""Personal-site rendering and text-portrait generation for asm-web.

The assembly server never renders anything; this module turns a profile and a
theme into the exact HTML that gets compressed into the ELF. Portraits are
generated locally as text, so the page stays self-contained and tiny.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEMES_DIR = ROOT / "assets" / "themes"

THEMES = {
    "terminal": {
        "label": "Terminal",
        "motion": "boot overlay / scanlines / glitch / blinking cursor",
        "description": "CRT personal card: boot sequence, stencil portrait, selected work.",
    },
    "brutalist": {
        "label": "Brutalist",
        "motion": "marquee / staggered slam / hover invert",
        "description": "Hard-edged light page: oversized type, thick rules, portrait block.",
    },
    "editorial": {
        "label": "Editorial",
        "motion": "fade / rise / slow drift / underline",
        "description": "Quiet portfolio: serif display, paper/ink palette, framed portrait.",
    },
    "blueprint": {
        "label": "Blueprint",
        "motion": "scan line / clip reveal / clock",
        "description": "Technical sheet: grid, scan line, monospace manifest, cyan/amber portrait.",
    },
}


class SiteError(Exception):
    pass


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def safe_url(value) -> str:
    url = str(value or "").strip()
    low = url.lower()
    if low.startswith(("https://", "http://", "mailto:")) or url.startswith("#"):
        return esc(url)
    return "#"


def load_profile(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise SiteError(f"profile not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SiteError(f"profile is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SiteError("profile must be a JSON object")
    for key in ("name", "handle", "role", "tagline", "bio"):
        if not str(data.get(key, "")).strip():
            raise SiteError(f"profile needs a non-empty '{key}'")
    data["links"] = [x for x in data.get("links", []) if isinstance(x, dict)]
    data["projects"] = [x for x in data.get("projects", []) if isinstance(x, dict)]
    data["skills"] = [str(x) for x in data.get("skills", []) if str(x).strip()]
    data["_path"] = str(path)
    data["_dir"] = path.resolve().parent
    return data


def initials(profile: dict) -> str:
    words = re.findall(r"[A-Za-z]+", str(profile.get("handle") or profile.get("name") or ""))
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if words:
        return words[0][:2].upper()
    return str(profile.get("name", "?"))[:1]


def portrait_source(profile: dict) -> Path | None:
    value = profile.get("portrait")
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = Path(profile["_dir"]) / path
    return path


def portrait_art(
    profile: dict,
    style: str | None = None,
    width: int | None = None,
    threshold: int | None = None,
) -> tuple[str, dict]:
    path = portrait_source(profile)
    if path is None or not path.is_file():
        raise SiteError("profile needs a portrait image (or set 'portrait')")
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise SiteError("portrait generation needs Pillow: python -m pip install Pillow") from exc

    style = (style or profile.get("portrait_style") or "stencil").lower()
    width = int(width or profile.get("portrait_width") or 64)
    threshold = int(threshold or profile.get("portrait_threshold") or 112)
    if style not in ("stencil", "ascii", "block", "braille"):
        raise SiteError(f"unknown portrait style: {style}")
    if not 20 <= width <= 120:
        raise SiteError("portrait width must be between 20 and 120")

    image = Image.open(path).convert("L")
    height = max(8, round(width * (image.height / image.width) * 0.625))
    small = image.resize((width, height), Image.LANCZOS)
    invert = bool(profile.get("portrait_invert", False))

    if style == "stencil":
        art = "\n".join(
            "".join(
                "#" if (small.getpixel((x, y)) >= threshold) != invert else " "
                for x in range(width)
            ).rstrip()
            for y in range(height)
        )
    elif style == "ascii":
        chars = " .:-=+*#%@"
        if invert:
            chars = chars[::-1]
        art = "\n".join(
            "".join(
                chars[small.getpixel((x, y)) * (len(chars) - 1) // 255]
                for x in range(width)
            ).rstrip()
            for y in range(height)
        )
    elif style == "block":
        chars = " ░▒▓█"
        if invert:
            chars = chars[::-1]
        art = "\n".join(
            "".join(
                chars[small.getpixel((x, y)) * (len(chars) - 1) // 255]
                for x in range(width)
            ).rstrip()
            for y in range(height)
        )
    else:  # braille: 2x4 dots per character
        dots = image.resize((width * 2, height * 4), Image.LANCZOS)
        dot_bits = [
            (0, 0, 1), (0, 1, 2), (0, 2, 4), (0, 3, 64),
            (1, 0, 8), (1, 1, 16), (1, 2, 32), (1, 3, 128),
        ]
        lines = []
        for by in range(height):
            line = []
            for bx in range(width):
                code = 0x2800
                for dx, dy, bit in dot_bits:
                    bright = dots.getpixel((bx * 2 + dx, by * 4 + dy)) >= threshold
                    if bright != invert:
                        code |= bit
                line.append(chr(code))
            lines.append("".join(line).rstrip("⠀ "))
        art = "\n".join(lines)

    art = re.sub(r"\n{3,}", "\n\n", art.strip("\n"))
    if not art.replace(" ", "").replace("⠀", "").strip():
        raise SiteError("portrait produced no visible pixels; adjust threshold/width")
    return art, {
        "style": style,
        "width": width,
        "height": height,
        "chars": len(art),
        "bytes": len(art.encode("utf-8")),
        "threshold": threshold,
        "source": str(path),
    }


def portrait_html(profile: dict, style: str | None = None, width: int | None = None, threshold: int | None = None) -> tuple[str, dict]:
    art, meta = portrait_art(profile, style, width, threshold)
    return (
        f'<pre class="portrait" aria-hidden="true">{esc(art)}</pre>',
        meta,
    )


def links_html(profile: dict, compact: bool = False) -> str:
    rows = []
    for item in profile["links"]:
        label = str(item.get("label", "")).strip()
        if not label:
            continue
        note = "" if compact else f'<em>{esc(item.get("note", ""))}</em>'
        rows.append(
            f'<a class="link" href="{safe_url(item.get("url"))}">'
            f'<span>{esc(label)}</span>{note}<b>↗</b></a>'
        )
    return "\n".join(rows)


def projects_html(profile: dict, featured_only: bool = False) -> str:
    projects = profile["projects"]
    if featured_only:
        projects = [p for p in projects if p.get("featured")] or projects[:3]
    rows = []
    for index, item in enumerate(projects, 1):
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        url = safe_url(item.get("url", "#"))
        rows.append(
            f'<a class="project" href="{url}"><small>{index:02d}</small>'
            f'<h3>{esc(title)}</h3>'
            f'<p>{esc(item.get("desc", ""))}</p></a>'
        )
    return "\n".join(rows)


def skills_html(profile: dict) -> str:
    return "\n".join(f'<span class="skill">{esc(x)}</span>' for x in profile["skills"])


CLOCK_SCRIPT = """<script>(()=>{const e=document.getElementById('clock');if(!e)return;const f=()=>e.textContent=new Date().toLocaleTimeString('zh-CN',{hour12:false});f();setInterval(f,1000)})();</script>"""


def _minify_css(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{}:;,>])\s*", r"\1", css)
    return css.strip()


def minify_css_in_html(page: bytes) -> bytes:
    text = page.decode("utf-8")

    def repl(match: re.Match) -> str:
        css = _minify_css(match.group(1))
        if css.count("{") != css.count("}"):
            raise SiteError("theme CSS has unbalanced braces")
        if any(ord(ch) < 32 and ch not in (chr(10), chr(13), chr(9)) for ch in css):
            raise SiteError("theme CSS contains control characters")
        return "<style>" + css + "</style>"

    text = re.sub(r"<style>(.*?)</style>", repl, text, flags=re.DOTALL)
    return text.encode("utf-8")


def render_theme(
    profile: dict,
    theme: str,
    *,
    portrait_style: str | None = None,
    portrait_width: int | None = None,
    portrait_threshold: int | None = None,
    year: int | None = None,
) -> tuple[bytes, dict]:
    if theme not in THEMES:
        raise SiteError(f"unknown theme: {theme}")
    template_path = THEMES_DIR / f"{theme}.html"
    if not template_path.is_file():
        raise SiteError(f"theme template not found: {template_path}")
    template = template_path.read_text(encoding="utf-8")

    portrait, portrait_meta = portrait_html(
        profile, portrait_style, portrait_width, portrait_threshold
    )
    about = profile.get("about", [])
    if isinstance(about, str):
        about = [about]
    about_html = "".join(
        f"<p>{esc(x)}</p>" for x in about if str(x).strip()
    )
    about_short_html = "".join(
        f"<p>{esc(x)}</p>" for x in about[:2] if str(x).strip()
    )
    values = {
        "NAME": esc(profile["name"]),
        "HANDLE": esc(profile["handle"]),
        "ROLE": esc(profile["role"]),
        "TAGLINE": esc(profile["tagline"]),
        "BIO": esc(profile["bio"]),
        "ABOUT": about_html,
        "ABOUT_SHORT": about_short_html,
        "LOCATION": esc(profile.get("location", "")),
        "STATUS": esc(profile.get("status", "")),
        "INITIALS": esc(initials(profile)),
        "PORTRAIT": portrait,
        "LINKS": links_html(profile),
        "LINKS_COMPACT": links_html(profile, compact=True),
        "PROJECTS": projects_html(profile),
        "FEATURED_PROJECTS": projects_html(profile, featured_only=True),
        "SKILLS": skills_html(profile),
        "CLOCK_SCRIPT": CLOCK_SCRIPT,
        "YEAR": str(year or profile.get("year") or 2026),
    }

    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise SiteError(f"theme {theme} uses unknown placeholder: {key}")
        return values[key]

    page = re.sub(r"\{\{([A-Z_]+)\}\}", replace, template).encode("utf-8")
    page = minify_css_in_html(page)
    return page, {
        "theme": theme,
        "theme_label": THEMES[theme]["label"],
        "motion": THEMES[theme]["motion"],
        "portrait": portrait_meta,
        "projects": len(profile["projects"]),
        "links": len([x for x in profile["links"] if str(x.get("label", "")).strip()]),
    }


def portrait_preview(image_path: Path, style: str, width: int, threshold: int) -> str:
    image_path = Path(image_path).resolve()
    profile = {
        "name": "PORTRAIT",
        "handle": "PREVIEW",
        "role": "",
        "tagline": "",
        "bio": "",
        "portrait": str(image_path),
        "_dir": str(Path(image_path).resolve().parent),
        "portrait_style": style,
        "portrait_width": width,
        "portrait_threshold": threshold,
    }
    art, _ = portrait_art(profile, style, width, threshold)
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>portrait preview</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#08090c}}
pre{{margin:0;color:#ffb86b;font:600 8px/1 ui-monospace,monospace;white-space:pre}}</style>
<pre>{esc(art)}</pre></html>"""
