import jsQR from 'jsqr';
import initBrotli, {
  BrotliStreamResultCode,
  DecompressStream,
} from 'brotli-dec-wasm/web';
import brotliWasmUrl from 'brotli-dec-wasm/web/bg.wasm';

const $ = (id) => document.getElementById(id);
const encoder = new TextEncoder();
const utf8 = new TextDecoder('utf-8', { fatal: false });
const params = new URLSearchParams(location.search);

const HTTP_10 = encoder.encode('HTTP/1.0 200 OK\r\n');
const HTTP_11 = encoder.encode('HTTP/1.1 200 OK\r\n');
const CRLFCRLF = encoder.encode('\r\n\r\n');
const CSP =
  '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'; img-src data:; font-src data:">';

const els = {
  home: $('home'),
  scan: $('scan'),
  result: $('result'),
  homeStatus: $('home-status'),
  scanStatus: $('scan-status'),
  scanBtn: $('scan-btn'),
  scanStop: $('scan-stop'),
  demoBtn: $('demo-btn'),
  qrFile: $('qr-file'),
  elfFile: $('elf-file'),
  drop: $('drop'),
  video: $('video'),
  resultTitle: $('result-title'),
  resultMeta: $('result-meta'),
  frame: $('page'),
  backBtn: $('back-btn'),
  fullscreenBtn: $('fullscreen-btn'),
  downloadBtn: $('download-btn'),
  downloadHtmlBtn: $('download-html-btn'),
  testInfo: $('test-info'),
};

const canvas = document.createElement('canvas');
const ctx = canvas.getContext('2d', { willReadFrequently: true });
const state = {
  stream: null,
  timer: null,
  current: null,
  busy: false,
};

let brotliModule = null;

function findBytes(haystack, needle, start = 0) {
  outer: for (let i = start; i <= haystack.length - needle.length; i++) {
    for (let j = 0; j < needle.length; j++) {
      if (haystack[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

function concatBytes(parts) {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

function setStatus(el, message, kind = '') {
  el.textContent = message || '';
  el.hidden = !message;
  el.dataset.kind = kind;
}

function setHomeStatus(message, kind = '') {
  setStatus(els.homeStatus, message, kind);
}

function debug(stage, extra = '') {
  document.body.dataset.stage = stage;
  els.testInfo.textContent = `${stage}${extra ? ` ${extra}` : ''}`;
}

function showView(name) {
  for (const [key, el] of Object.entries({
    home: els.home,
    scan: els.scan,
    result: els.result,
  })) {
    el.hidden = key !== name;
  }
  document.body.classList.toggle('result-mode', name === 'result');
  if (name !== 'scan') stopScan();
  window.scrollTo(0, 0);
}

function stopScan() {
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = null;
  }
  if (state.stream) {
    for (const track of state.stream.getTracks()) track.stop();
    state.stream = null;
  }
  els.video.srcObject = null;
}

function drawVideo() {
  const width = els.video.videoWidth;
  const height = els.video.videoHeight;
  if (!width || !height) return null;
  const max = 1600;
  const scale = Math.min(1, max / Math.max(width, height));
  const w = Math.max(1, Math.round(width * scale));
  const h = Math.max(1, Math.round(height * scale));
  canvas.width = w;
  canvas.height = h;
  ctx.drawImage(els.video, 0, 0, w, h);
  return { w, h };
}

function scanFrame() {
  if (state.busy) return;
  const size = drawVideo();
  if (!size) return;
  const image = ctx.getImageData(0, 0, size.w, size.h);
  const code = jsQR(image.data, size.w, size.h, {
    inversionAttempts: 'dontInvert',
  });
  if (!code?.binaryData?.length) return;
  state.busy = true;
  stopScan();
  handleQrBytes(code.binaryData, 'QR camera')
    .catch(showError)
    .finally(() => {
      state.busy = false;
    });
}

async function startScan() {
  if (state.busy) return;
  if (!navigator.mediaDevices?.getUserMedia) {
    setHomeStatus('这个浏览器不支持摄像头。可以上传二维码图片。', 'error');
    return;
  }
  stopScan();
  showView('scan');
  setStatus(els.scanStatus, '正在请求摄像头…');
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
    });
    els.video.srcObject = state.stream;
    await els.video.play();
    setStatus(els.scanStatus, '把二维码放进框里，保持稳定…');
    state.timer = setInterval(scanFrame, 160);
  } catch (error) {
    stopScan();
    showView('home');
    setHomeStatus(`无法打开摄像头：${error?.message || error}`, 'error');
  }
}

async function handleQrBytes(raw, source) {
  const bytes = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
  if (!bytes.length) {
    throw new Error('二维码里没有二进制数据。这不是 asm-web 生成的 ELF。');
  }
  await handleElfBytes(bytes, source);
}

async function handleElfBytes(bytes, source) {
  if (
    bytes.length < 64 ||
    bytes[0] !== 0x7f ||
    bytes[1] !== 0x45 ||
    bytes[2] !== 0x4c ||
    bytes[3] !== 0x46
  ) {
    throw new Error('这不是 ELF 文件。asm-web 二维码里应该是一段 ELF。');
  }
  debug('elf:valid', `bytes=${bytes.length}`);
  const page = await elfToPage(bytes, source);
  state.current = page;
  renderPage(page);
}

async function elfToPage(elfBytes, source) {
  let headerStart = -1;
  for (const marker of [HTTP_10, HTTP_11]) {
    const found = findBytes(elfBytes, marker);
    if (found >= 0 && (headerStart < 0 || found < headerStart)) {
      headerStart = found;
    }
  }
  if (headerStart < 0) {
    throw new Error('ELF 里没有找到 HTTP/1.x 200 OK 响应。');
  }
  const headerEnd = findBytes(elfBytes, CRLFCRLF, headerStart);
  if (headerEnd < 0) {
    throw new Error('HTTP 响应头没有以 CRLFCRLF 结束。');
  }
  const headerText = new TextDecoder('latin1').decode(
    elfBytes.subarray(headerStart, headerEnd + 4)
  );
  const lines = headerText.split('\r\n');
  const headers = {};
  for (const line of lines.slice(1)) {
    const colon = line.indexOf(':');
    if (colon > 0) {
      headers[line.slice(0, colon).trim().toLowerCase()] = line
        .slice(colon + 1)
        .trim();
    }
  }

  let body = elfBytes.subarray(headerEnd + 4);
  debug('http:found', `body=${body.length}`);
  const contentLength = Number.parseInt(headers['content-length'] || '', 10);
  if (Number.isFinite(contentLength) && contentLength > 0) {
    body = body.subarray(0, contentLength);
  }

  const encoding = (headers['content-encoding'] || '').toLowerCase();
  let pageBytes;
  let codec;
  if (encoding.includes('br')) {
    pageBytes = await brotliDecompress(body);
    codec = 'brotli';
  } else if (encoding.includes('gzip')) {
    pageBytes = await gunzip(body);
    codec = 'gzip';
  } else {
    try {
      pageBytes = await brotliDecompress(body);
      codec = 'brotli';
    } catch {
      pageBytes = await gunzip(body);
      codec = 'gzip';
    }
  }

  debug('page:decoded', `bytes=${pageBytes.length}`);
  let html = utf8.decode(pageBytes);
  if (params.has('nointro')) html = addNoIntro(html);
  return {
    elfBytes,
    pageBytes,
    html,
    meta: {
      source,
      codec,
      status: lines[0],
      headers,
      headerBytes: headerEnd + 4 - headerStart,
      payloadBytes: body.length,
    },
  };
}

async function getBrotli() {
  if (!brotliModule) {
    brotliModule = initBrotli(brotliWasmUrl).then(() => ({
      DecompressStream,
      BrotliStreamResultCode,
    }));
  }
  return brotliModule;
}

async function brotliDecompress(body) {
  const brotli = await getBrotli();
  const stream = new brotli.DecompressStream();
  const chunks = [];
  try {
    // The ELF carries server code after the Brotli stream. Feed one byte at a
    // time so we stop exactly at the stream end and never hand the trailing
    // machine code to the decoder.
    for (let i = 0; i < body.length; i++) {
      const result = stream.decompress(body.subarray(i, i + 1), 262144);
      if (result.buf && result.buf.length) chunks.push(result.buf);
      if (result.code === brotli.BrotliStreamResultCode.ResultSuccess) {
        return concatBytes(chunks);
      }
      if (result.code === brotli.BrotliStreamResultCode.NeedsMoreOutput) {
        throw new Error('Brotli 输出缓冲区不足');
      }
      if (result.code !== brotli.BrotliStreamResultCode.NeedsMoreInput) {
        throw new Error('Brotli 解码失败');
      }
    }
  } catch (error) {
    throw new Error(`Brotli 解码失败：${error?.message || error}`);
  } finally {
    try {
      stream.free();
    } catch {
      /* ignore */
    }
  }
  throw new Error('Brotli 数据不完整，无法解开页面。');
}

async function gunzip(body) {
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('这个浏览器不支持 gzip 解压。');
  }
  let lastError = null;
  // The ELF may carry server code after the gzip stream. Try the exact body,
  // then trim up to 512 trailing bytes.
  for (let trim = 0; trim <= 512; trim += trim < 64 ? 1 : 8) {
    const slice = trim ? body.subarray(0, body.length - trim) : body;
    try {
      const stream = new Blob([slice]).stream().pipeThrough(
        new DecompressionStream('gzip')
      );
      const buffer = await new Response(stream).arrayBuffer();
      return new Uint8Array(buffer);
    } catch (error) {
      lastError = error;
    }
  }
  throw new Error(`gzip 解压失败：${lastError?.message || lastError}`);
}

function addNoIntro(html) {
  const style =
    '<style>*{animation:none!important;transition:none!important}.b,.boot{display:none!important}</style>';
  return /<\/head>/i.test(html)
    ? html.replace(/<\/head>/i, `${style}</head>`)
    : `${style}${html}`;
}

function injectCsp(html) {
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/<head[^>]*>/i, (head) => `${head}${CSP}`);
  }
  return `<!doctype html><html><head><meta charset="utf-8">${CSP}</head><body>${html}</body></html>`;
}

function extractTitle(html) {
  const match = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  if (!match) return '';
  const textarea = document.createElement('textarea');
  textarea.innerHTML = match[1];
  return textarea.value.replace(/\s+/g, ' ').trim().slice(0, 120);
}

function addMeta(label, value) {
  const span = document.createElement('span');
  const bold = document.createElement('b');
  bold.textContent = value;
  span.append(`${label} `, bold);
  return span;
}

function renderPage(page) {
  const title = extractTitle(page.html) || 'Decoded page';
  els.resultTitle.textContent = title;
  els.resultMeta.replaceChildren(
    addMeta('ELF', `${page.elfBytes.length.toLocaleString()} B`),
    addMeta('page', `${page.pageBytes.length.toLocaleString()} B`),
    addMeta('codec', page.meta.codec),
    addMeta('source', page.meta.source)
  );
  els.frame.srcdoc = injectCsp(page.html);
  showView('result');
  document.body.dataset.viewerState = 'result';
  document.body.dataset.pageBytes = String(page.pageBytes.length);
  document.body.dataset.elfBytes = String(page.elfBytes.length);
  document.body.dataset.codec = page.meta.codec;
  document.body.dataset.title = title;
  debug('result', `page=${page.pageBytes.length}`);
}

function download(bytes, name, type) {
  const blob = new Blob([bytes], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function showError(error) {
  console.error(error);
  stopScan();
  showView('home');
  setHomeStatus(error?.message || String(error), 'error');
  document.body.dataset.viewerState = 'error';
  document.body.dataset.stage = 'error';
  els.testInfo.textContent = `error ${error?.stack || error?.message || String(error)}`;
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('图片加载失败'));
    image.src = url;
  });
}

function decodeImageElement(image) {
  const max = 2000;
  const scale = Math.min(
    1,
    max / Math.max(image.naturalWidth, image.naturalHeight)
  );
  const width = Math.max(1, Math.round(image.naturalWidth * scale));
  const height = Math.max(1, Math.round(image.naturalHeight * scale));
  canvas.width = width;
  canvas.height = height;
  ctx.drawImage(image, 0, 0, width, height);
  const data = ctx.getImageData(0, 0, width, height);
  const code = jsQR(data.data, width, height, {
    inversionAttempts: 'attemptBoth',
  });
  if (!code?.binaryData?.length) {
    throw new Error('没有识别到二维码。请上传清晰的二维码 PNG/JPG。');
  }
  return code;
}

async function decodeImageFile(file) {
  if (state.busy) return;
  state.busy = true;
  setHomeStatus('正在识别二维码…');
  const url = URL.createObjectURL(file);
  try {
    const image = await loadImage(url);
    const code = decodeImageElement(image);
    debug('qr:decoded', `raw=${code.binaryData.length}`);
    await handleQrBytes(code.binaryData, 'QR image');
    setHomeStatus('');
  } catch (error) {
    showError(error);
  } finally {
    URL.revokeObjectURL(url);
    state.busy = false;
  }
}

async function decodeElfFile(file) {
  if (state.busy) return;
  state.busy = true;
  setHomeStatus('正在解析 ELF…');
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    await handleElfBytes(bytes, 'ELF file');
    setHomeStatus('');
  } catch (error) {
    showError(error);
  } finally {
    state.busy = false;
  }
}

async function runDemo() {
  if (state.busy) return;
  state.busy = true;
  setHomeStatus('正在解码示例二维码…');
  try {
    debug('demo:start');
    const image = await loadImage('./demo-qr.png');
    const code = decodeImageElement(image);
    debug('qr:decoded', `raw=${code.binaryData.length}`);
    await handleQrBytes(code.binaryData, 'QR demo');
    setHomeStatus('');
  } catch (error) {
    showError(error);
  } finally {
    state.busy = false;
  }
}

function goHome() {
  stopScan();
  state.current = null;
  els.frame.srcdoc = '';
  document.body.classList.remove('result-mode');
  showView('home');
  document.body.dataset.viewerState = 'home';
  setHomeStatus('');
}

function wireUi() {
  els.scanBtn.addEventListener('click', startScan);
  els.scanStop.addEventListener('click', goHome);
  els.backBtn.addEventListener('click', goHome);
  els.demoBtn.addEventListener('click', runDemo);

  els.qrFile.addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) decodeImageFile(file);
  });
  els.elfFile.addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) decodeElfFile(file);
  });

  document.addEventListener('dragover', (event) => {
    event.preventDefault();
    els.drop.classList.add('active');
  });
  document.addEventListener('dragleave', (event) => {
    if (event.target === document.documentElement) {
      els.drop.classList.remove('active');
    }
  });
  document.addEventListener('drop', (event) => {
    event.preventDefault();
    els.drop.classList.remove('active');
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    if (/\.elf$/i.test(file.name) || file.type === 'application/octet-stream') {
      decodeElfFile(file);
    } else {
      decodeImageFile(file);
    }
  });

  els.downloadBtn.addEventListener('click', () => {
    if (state.current) {
      download(state.current.elfBytes, 'site.elf', 'application/octet-stream');
    }
  });
  els.downloadHtmlBtn.addEventListener('click', () => {
    if (state.current) {
      download(state.current.html, 'site.html', 'text/html;charset=utf-8');
    }
  });
  els.fullscreenBtn.addEventListener('click', async () => {
    try {
      if (!document.fullscreenElement) {
        await els.result.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch {
      /* fullscreen can be denied */
    }
  });

  if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
    navigator.serviceWorker.register('./sw.js').catch((error) => {
      console.debug('service worker:', error);
    });
  }

  if (params.has('demo')) {
    setTimeout(runDemo, 80);
  }
}

wireUi();
document.body.dataset.viewerState = 'home';
