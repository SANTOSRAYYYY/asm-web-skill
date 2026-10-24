# Animation playbook for a 3 KB site

The page is not limited by raw HTML size; it is limited by the **Brotli-compressed** size that has to fit in the ELF. The reference demo is 7,932 B of HTML (7,807 B minified) and 2,616 B of Brotli. That leaves 33 B in the 2,953 B QR budget.

Read this before designing a page or when `size` says the payload is over.

## The budget

Default build overhead is 304 B (120 ELF headers + 5 call + 66 HTTP headers + 113 asm). That leaves **2,649 B** for the compressed page. Options change it:

- `--no-drain`: +15 B payload room
- `--length`: -22 B
- `--host 0.0.0.0`: +12 B
- `--minify`: usually a few bytes either way; measure

A page can be 8-10 KB raw and still fit if it is mostly text/CSS. A single base64 image or web font can eat the whole budget.

## Measure the right number

```bash
python scripts/asmweb.py size assets/example/index.html
python scripts/asmweb.py size assets/example/index.html --minify
```

`size` prints as-is and minified HTML, Brotli q11, gzip -9, the exact ELF total, and headroom. Judge the compressed payload. Run it after every meaningful change.

Minification is not automatically a win: Brotli already encodes whitespace runs well, and removing structure can lose patterns. The demo happens to improve from 2,631 B to 2,616 B; earlier versions got *worse* after minify. Always compare.

## Effects that compress well

### Gradients and transforms

```css
body:before{content:"";position:fixed;inset:-40%;background:
  radial-gradient(35% 35% at 25% 25%,#33ff9926,transparent 70%),
  radial-gradient(30% 30% at 75% 70%,#5ce1ff26,transparent 70%);
  animation:bg 24s linear infinite}
@keyframes bg{to{transform:rotate(360deg)}}
```

Two radial gradients plus a rotation give a living background for ~250 bytes. Animated `background-position` or `filter` can work too, but `transform`/`opacity` are cheaper to run.

### Pseudo-elements for texture

Scanlines and grids are one fixed layer:

```css
body:after{content:"";position:fixed;inset:0;pointer-events:none;
  background:repeating-linear-gradient(0deg,#0000 0 2px,#00000038 2px 3px);
  opacity:.35}
```

No images. The whole effect is a few dozen bytes.

### Glitch without duplicate text

Use `attr()` so the glitch layers do not repeat the headline in the HTML:

```css
h1 em{background:linear-gradient(100deg,#33ff99,#5ce1ff,#c084fc);
  -webkit-background-clip:text;background-clip:text;color:transparent;position:relative}
h1 em:before,h1 em:after{content:attr(data-t);position:absolute;inset:0;
  background:#05060a;opacity:0;animation:glitch 4s infinite steps(1)}
h1 em:before{color:#33ff99;clip-path:inset(0 0 52% 0)}
h1 em:after{color:#5ce1ff;clip-path:inset(48% 0 0 0)}
@keyframes glitch{0%,91%,100%{opacity:0;transform:none}
  92%{opacity:.75;transform:translate(-3px,1px)}
  94%{opacity:.6;transform:translate(3px,-1px)}}
```

`<em data-t="写进汇编">写进汇编</em>` is the only copy of the text.

### 3D cube

```css
.hero{perspective:900px}
.cube{position:relative;width:150px;aspect-ratio:1;transform-style:preserve-3d;
  animation:spin 16s linear infinite}
.cube i{position:absolute;inset:0;border:1px solid #33ff9966;
  background:linear-gradient(135deg,#33ff9914,#5ce1ff0a)}
.cube i:nth-child(1){transform:translateZ(75px)}
/* ...5 more faces... */
@keyframes spin{from{transform:rotateX(-15deg) rotateY(-25deg)}
  to{transform:rotateX(345deg) rotateY(335deg)}}
```

Important: do **not** put `filter` on an element with `transform-style:preserve-3d`; it forces flattening and the cube renders as a square. Put glow on the faces or the page background instead. Add `perspective` to an ancestor, and start the keyframes at an angled rotation so a screenshot shows depth.

### Boot overlay

A CSS-only boot sequence is a strong intro and costs no JS:

```css
.boot{position:fixed;inset:0;z-index:9;display:grid;place-content:center;
  background:#030407;font:600 12px/1.8 ui-monospace,monospace;color:#33ff99;
  animation:boot 1.1s 2.9s forwards}
@keyframes boot{to{opacity:0;visibility:hidden}}
@media(prefers-reduced-motion:reduce){*{animation:none!important}.boot{display:none}}
```

For screenshots, either wait ~4 s or temporarily add `style="display:none"` to the boot div; do not ship a query-string hack that costs bytes.

### A clock in ~100 bytes

```html
<b id="t">--:--:--</b>
<script>
const t=document.getElementById('t'),f=()=>t.textContent=new Date().toLocaleTimeString('zh-CN',{hour12:false});
f();setInterval(f,1000);
</script>
```

It gives the page motion for very few bytes and needs no libraries.

### CSS-only typography motion

- Blinking cursor: `animation:blink 1s steps(1) infinite`.
- Marquee: duplicate one line and animate `translateX(-50%)`; keep the text short.
- Reveal on hover: `transition:transform .2s,opacity .2s` with a `transform` on `:hover`.
- Scroll snap / sticky headers: no animation bytes, but beware mobile.

## What costs too much

- **Images** (even small PNGs): a 10 KB base64 image is over budget before CSS.
- **Web fonts**: a subset Woff2 is still hundreds of bytes, and base64 adds 33%.
- **Framework/CDN scripts**: React/Vue/GSAP/Tailwind CDN builds do not fit; the original `qr-server` page loads GSAP and Flip from unpkg, which breaks the offline promise.
- **Large unicode/ASCII art**: the original page's box-drawing logo is visually great but costly. Prefer a short wordmark or simple SVG.
- **Repeated long class names**: Brotli compresses repetition, but short names still help. The demo uses one- and two-letter classes.
- **Comments and meta bloat**: keep `charset`, `viewport`, and `title`; drop Open Graph, favicons, analytics, and comments.
- **Huge inline SVGs**: trace or simplify; every path is bytes.

## The demo, measured

Source: 7,932 B raw; 7,807 B after conservative minify.

| Slice | Bytes |
| --- | ---: |
| Boot overlay + terminal copy | small CSS + ~100 B text |
| Hero, glitch, gradient title | ~1.5 KB CSS |
| 3D cube (6 faces) | ~0.6 KB CSS |
| Grid/scanlines/background | ~0.4 KB CSS |
| Stats, cards, footer | ~2 KB HTML/CSS |
| Live clock JS | ~120 B |
| **Brotli payload** | **2,616 B** |
| **ELF total** | **2,920 B** |
| **Headroom** | **33 B** |

The page has no images, no fonts, no external requests, and a 4-second boot animation. That is the trade: dense CSS for a small compressed payload.

## Testing checklist

1. `size` shows positive headroom (keep 30-50 B if you will edit again).
2. No external references (the command warns).
3. Page is UTF-8 and has `<meta charset=utf-8>`.
4. Boot overlay disappears; `prefers-reduced-motion` hides it.
5. Layout works at 1200 px and 500 px; the cube hides on narrow screens.
6. `build --v40 --minify --emulate` passes.
7. `extract` + `cmp` proves the QR contains the exact page and ELF.
8. On Linux, run the ELF and fetch `/`; on Windows, use the emulator and CI.

## Common traps

- "It's only 8 KB of HTML, it must fit." Compressed size is what matters.
- "Minify always helps." See above; measure.
- "I'll add one font/logo." That is usually the whole budget.
- "The original page uses GSAP, so I can too." The original page is **not** offline; its CDN scripts are a runtime dependency. This skill requires zero external requests.
- "The cube disappeared/flattened." Remove `filter` from the preserve-3d element; add `perspective` to the parent.
- "The screenshot only shows the boot screen." Wait for the overlay or hide it in a preview copy.
- "The page is over by 20 bytes; add `--length` anyway." `--length` costs 22 B and makes it worse.
- "Bind 0.0.0.0 to test from my phone." That exposes the page to the LAN; use it only when requested.
