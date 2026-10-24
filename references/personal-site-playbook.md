# Personal site playbook

`asmweb.py personal` turns one `profile.json` into a complete personal site, then uses the asm-web engine to build the ELF and QR. The page is still a single self-contained HTML file; the profile is content, not a layout generator.

## Quick start

```bash
python scripts/asmweb.py themes
python scripts/asmweb.py portrait assets/example/portrait.png --style stencil --width 64 --out portrait.html
python scripts/asmweb.py personal assets/example/profile.json --theme terminal -o dist/personal --emulate
```

Use `--render-only` while iterating. It writes the exact page and reports the ELF size without generating a QR:

```bash
python scripts/asmweb.py personal assets/example/profile.json --theme editorial -o dist/personal --render-only
```

Open `dist/personal/<theme>/<theme>.page.html` in a browser to see the animations. The boot overlay hides after a few seconds. The built QR contains the ELF; `extract --html` recovers the same page, and the ELF runs natively on Linux x86_64.

## Profile schema

```json
{
  "name": "林一",
  "handle": "LIN YI",
  "role": "systems engineer · low-level & web",
  "tagline": "把系统写到接近金属。",
  "bio": "One or two sentences for the hero.",
  "about": ["Paragraph one.", "Paragraph two.", "Paragraph three."],
  "location": "SHANGHAI · UTC+8",
  "status": "OPEN TO WORK",
  "links": [
    {"label": "GitHub", "url": "https://github.com/you", "note": "开源与实验"},
    {"label": "Email", "url": "mailto:you@example.com", "note": "聊点有意思的"}
  ],
  "projects": [
    {"title": "Project", "desc": "One or two sentences.", "url": "https://example.com", "featured": true}
  ],
  "skills": ["C", "Go", "x86_64 ASM"],
  "portrait": "portrait.png",
  "portrait_style": "stencil",
  "portrait_width": 64,
  "portrait_threshold": 112,
  "portrait_invert": false
}
```

- Required: `name`, `handle`, `role`, `tagline`, `bio`.
- `links` and `projects` are optional lists; URLs are limited to `http://`, `https://`, `mailto:`, or `#`.
- `featured: true` marks the projects shown by all four themes. The themes use the first three featured projects; if fewer are marked, the first three projects are used. The profile can list more projects for future use or an archive link.
- `about` is rendered in full by editorial; brutalist and blueprint use the first two paragraphs. Terminal uses the bio only.
- `portrait` is resolved relative to the profile file. It is required by the current themes; use a high-contrast portrait, a silhouette, or an abstract image. The image is processed locally and never uploaded.
- `portrait_style`: `stencil` (default), `ascii`, `block`, `braille`. `portrait_invert` is useful on light themes.

## Themes

All four themes use the same content model and a text portrait. They show three featured projects to stay inside the 2,953-byte ELF limit. Numbers below are from the example profile/portrait.

| Theme | Motion | Page | Payload | ELF | Headroom | QR |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| terminal | boot overlay, scanlines, glitch, blinking cursor, portrait scan-in | 8,309 B | 2,586 B | 2,890 B | 63 B | v40-L |
| brutalist | marquee, staggered slam, hover invert, portrait stamp | 8,684 B | 2,575 B | 2,879 B | 74 B | v40-L |
| editorial | fade/rise, slow portrait drift, underline links | 8,038 B | 2,604 B | 2,908 B | 45 B | v40-L |
| blueprint | scan line, clip reveal, clock | 7,849 B | 2,529 B | 2,833 B | 120 B | v40-L |

The terminal/brutalist/editorial pages are near the cap; the example is intentionally dense. Editing copy can push them over. Run `--render-only` after every change and keep 30-50 B of headroom for another edit.

`personal` uses the HTML as-is; do **not** pass `--minify` by default. Sitegen already minifies CSS, and the HTML minifier can make the Brotli payload larger (or smaller) depending on the theme. Measure both if you change the content.

## Text portraits

The portrait is generated at build time from a photo or any high-contrast image:

```bash
python scripts/asmweb.py portrait portrait.png --style stencil --width 64 --out portrait.html
python scripts/asmweb.py portrait portrait.png --style ascii --width 56 --threshold 110 --out ascii.html
```

- **stencil** (1-bit `#`): best recognizability per byte, classic terminal look; ~190-260 B compressed in the demo.
- **ascii** (`.:-=+*#%@`): more "text art", usually 40-100 B larger than stencil.
- **block** (`░▒▓█`): graphic and dense; at 56×36/8px it can muddy facial features.
- **braille** (2×4 dots per character): high dot resolution but small and font-dependent; use only when the audience/context supports it.
- A bitmap + JavaScript renderer was measured and is **worse** than pre-rendered text (base64 + JS costs more than the text grid), so it is not implemented.

Portrait tips:

- Start with a high-contrast, front-facing crop, plain background, face filling most of the frame. The example image is synthetic.
- `portrait_width` 48-72 is the practical range. Height is derived from the image aspect and the monospace cell ratio.
- Tune `portrait_threshold` (stencil/braille) or `portrait_invert` (light themes) and preview before building.
- The portrait is decorative: the templates set `aria-hidden="true"`; the name/role remain real text for screen readers.
- A portrait is a likeness. The QR is public; process locally and get consent if it is someone else's face.
- If you don't want a face, use a silhouette, monogram image, abstract pattern, or a high-contrast crop of your work. The same pipeline works.

## Intake

Use two short rounds, not a 20-field form.

Round 1 (choices):

1. **Content depth**: 3 featured projects in an animated theme, or a larger custom page? The four themes are curated for 3.
2. **Audience and CTA**: who visits, and what should they do (contact, see work, GitHub, remember you)?
3. **Theme/motion**: terminal / brutalist / editorial / blueprint. Show the previews and the byte costs.
4. **Portrait**: do you have a photo or high-contrast image? Is a stylized likeness acceptable? If not, use a monogram/abstract image.

Round 2 (content):

- Name, handle, role, one-line tagline.
- Bio (1-2 sentences) and 2-3 About paragraphs, or bullet points to draft from.
- Three featured projects: title, 1-2 sentence description, URL.
- Links: GitHub/email/site; notes are optional.
- Location/status, language (Chinese/English; bilingual copy costs more), and anything that must not be public.

Then show the theme previews/screenshots and one round of feedback. Keep the user's voice; do not invent links, employers, or claims. Explicitly explain what will not fit: photos, web fonts, many projects, long bilingual copy, and heavy decoration together.

## Build and verify

```bash
python scripts/asmweb.py personal assets/example/profile.json --theme terminal -o dist/personal --emulate
python scripts/asmweb.py extract dist/personal/terminal/terminal.png -o /tmp/card.elf --html /tmp/card.html
python scripts/asmweb.py inspect /tmp/card.elf
```

- `--emulate` runs two connections in Unicorn and compares the served page.
- On Linux x86_64: `chmod +x dist/personal/terminal/terminal.elf && ./dist/personal/terminal/terminal.elf`, then open `http://127.0.0.1:8080`.
- The QR is a data QR. A normal scanner sees binary; use `extract --html`/`inspect`, or run the ELF on Linux. A portable browser viewer/PWA is not part of this build yet; the page snapshot is the local preview.
- The example profile and portrait are fictional placeholders. Replace `assets/example/profile.json` and `assets/example/portrait.png` with the user's content.

## Current limits

- 2,953 B total ELF; the themes fit 3 featured projects + portrait. A simple 5-project page can fit with a lighter layout (tested separately), but the four animated themes use 3.
- No images, web fonts, CDNs, or external requests in the page. The portrait is text.
- The server is a local demo: HTTP/1.0, no TLS/auth/request parsing, no keep-alive.
- The per-card browser viewer/PWA is not included yet; the QR is for native Linux or extraction.
