/* 萬能影片下載器 · 前端邏輯
   與 Python 端透過 window.pywebview.api 溝通；
   Python 端以 window.__push(event, payload) 回推事件。 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const el = {
    url: $('url'), paste: $('btnPaste'),
    fmtSelect: $('fmtSelect'), fmtBtn: $('fmtBtn'), fmtLabel: $('fmtLabel'), fmtMenu: $('fmtMenu'),
    savePath: $('savePath'), change: $('btnChange'), open: $('btnOpen'),
    start: $('btnStart'), cancel: $('btnCancel'), update: $('btnUpdate'),
    status: $('status'), pct: $('pct'), size: $('size'), fill: $('fill'),
    speed: $('speed'), eta: $('eta'), log: $('log'), ver: $('ver'), foot: $('foot'),
    clock: $('clock'), hHour: $('hHour'), hMin: $('hMin'), hSec: $('hSec'),
    weekday: $('weekday'), date: $('date'),
  };

  const state = { formats: [], format: 'best', saveDir: '', busy: false, updating: false, inited: false };
  const api = () => window.pywebview.api;

  /* ───────── helpers ───────── */
  const pad = (n) => String(n).padStart(2, '0');
  const now = () => { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };

  function fmtBytes(b) {
    if (!b && b !== 0) return '—';
    const u = ['B', 'KB', 'MB', 'GB'];
    let i = 0, v = b;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
  }
  function fmtSpeed(s) { return s ? `${fmtBytes(s)}/s` : '—'; }
  function fmtEta(sec) {
    if (sec === null || sec === undefined || isNaN(sec)) return '—';
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return `${sec} 秒`;
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return `${m} 分 ${pad(s)} 秒`;
    return `${Math.floor(m / 60)} 時 ${pad(m % 60)} 分`;
  }

  function setStatus(text, kind) {
    el.status.textContent = text;
    el.status.className = 'status' + (kind ? ' ' + kind : '');
  }
  function addLog(text, level) {
    if (!text) return;
    const line = document.createElement('div');
    line.className = 'line ' + (level || 'info');
    const t = document.createElement('span'); t.className = 't'; t.textContent = now();
    const m = document.createElement('span'); m.textContent = text;
    line.append(t, m);
    el.log.appendChild(line);
    while (el.log.children.length > 500) el.log.removeChild(el.log.firstChild);
    el.log.scrollTop = el.log.scrollHeight;
  }
  function setProgress(p) {
    p = Math.max(0, Math.min(100, p || 0));
    el.fill.classList.remove('indeterminate');
    el.fill.style.width = p + '%';
    el.pct.textContent = p.toFixed(p >= 100 ? 0 : 1);
  }
  function setIndeterminate() {
    el.fill.classList.add('indeterminate');
    el.pct.textContent = '…';
  }
  function resetProgress() {
    setProgress(0);
    el.size.textContent = '';
    el.speed.textContent = '—';
    el.eta.textContent = '—';
  }
  function setBusy(b) {
    state.busy = b;
    el.start.disabled = b || state.updating;
    el.cancel.disabled = !b;
    el.update.disabled = b || state.updating;
    el.url.disabled = b;
    el.paste.disabled = b;
    el.fmtBtn.disabled = b;
    el.fill.classList.toggle('busy', b);
    if (!b) el.fill.classList.remove('indeterminate');
  }
  function setUpdating(u) {
    state.updating = u;
    el.update.disabled = u || state.busy;
    el.update.textContent = u ? '更新中…' : '更新下載核心';
    el.start.disabled = u || state.busy;
  }
  function renderPath() {
    el.savePath.firstElementChild.textContent = state.saveDir || '—';
    el.savePath.title = state.saveDir || '';
  }
  function renderFormats() {
    el.fmtMenu.innerHTML = '';
    for (const f of state.formats) {
      const o = document.createElement('div');
      o.className = 'opt' + (f.key === state.format ? ' active' : '');
      o.textContent = f.label;
      o.dataset.key = f.key;
      o.addEventListener('click', () => chooseFormat(f.key));
      el.fmtMenu.appendChild(o);
    }
    const cur = state.formats.find((f) => f.key === state.format);
    el.fmtLabel.textContent = cur ? cur.label : state.format;
  }
  async function chooseFormat(key) {
    state.format = key;
    renderFormats();
    el.fmtSelect.classList.remove('open');
    try { await api().set_format(key); } catch (_) {}
  }

  /* ───────── clock ───────── */
  function buildClock() {
    for (let i = 0; i < 60; i++) {
      const t = document.createElement('div');
      t.className = 'tick' + (i % 5 === 0 ? ' major' : '');
      t.style.transform = `translate(-50%, -50%) rotate(${i * 6}deg) translateY(-51px)`;
      el.clock.insertBefore(t, el.clock.firstChild);
    }
  }
  function tickClock() {
    const d = new Date();
    const s = d.getSeconds(), m = d.getMinutes() + s / 60, h = (d.getHours() % 12) + m / 60;
    el.hSec.style.transform = `rotate(${s * 6}deg)`;
    el.hMin.style.transform = `rotate(${m * 6}deg)`;
    el.hHour.style.transform = `rotate(${h * 30}deg)`;
    el.weekday.textContent = d.toLocaleDateString('en-US', { weekday: 'long' });
    el.date.textContent = `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`;
  }

  /* ───────── actions ───────── */
  function looksLikeUrl(s) { return /^https?:\/\/\S+$/i.test(s); }

  async function doPaste() {
    try {
      const t = (await api().read_clipboard()) || '';
      if (t) { el.url.value = t; setStatus('已貼上連結，按「開始下載」即可。'); }
      else setStatus('剪貼簿裡沒有找到網址。', 'warn');
    } catch (e) { setStatus('無法讀取剪貼簿。', 'err'); }
    el.url.focus();
  }

  async function doStart() {
    const url = el.url.value.trim();
    if (!url) { setStatus('請先貼上影片連結。', 'warn'); el.url.focus(); return; }
    if (!looksLikeUrl(url)) { setStatus('連結格式不對，需以 http:// 或 https:// 開頭。', 'warn'); return; }
    setBusy(true);
    resetProgress();
    setIndeterminate();
    setStatus('正在解析影片資訊…');
    addLog(`開始：${url}`);
    let r;
    try { r = await api().start_download(url, state.format); }
    catch (e) { r = { ok: false, error: String(e) }; }
    if (!r || !r.ok) {
      setBusy(false);
      setProgress(0);
      setStatus(r && r.error ? r.error : '無法開始下載。', 'err');
    }
  }

  async function doCancel() {
    el.cancel.disabled = true;
    setStatus('正在取消…', 'warn');
    try { await api().cancel_download(); } catch (_) {}
  }

  async function doChange() {
    try {
      const p = await api().choose_folder();
      if (p) { state.saveDir = p; renderPath(); setStatus('儲存位置已更新。'); addLog(`儲存位置：${p}`); }
    } catch (e) { setStatus('無法開啟資料夾選擇視窗。', 'err'); }
  }

  async function doOpen() {
    try { await api().open_folder(); } catch (e) { setStatus('無法開啟資料夾。', 'err'); }
  }

  async function doUpdate() {
    setUpdating(true);
    setStatus('正在更新下載核心（yt-dlp）…');
    try {
      const r = await api().update_core();
      if (r && r.ok === false) { setUpdating(false); setStatus(r.error || '無法更新。', 'err'); }
    } catch (e) { setUpdating(false); setStatus('無法更新。', 'err'); }
  }

  /* ───────── events from Python ───────── */
  window.__push = (event, p) => {
    p = p || {};
    switch (event) {
      case 'progress': {
        if (p.phase === 'downloading') {
          if (p.total) setProgress(p.percent); else setIndeterminate();
          el.size.textContent = p.total ? `${fmtBytes(p.downloaded)} / ${fmtBytes(p.total)}` : fmtBytes(p.downloaded);
          el.speed.textContent = fmtSpeed(p.speed);
          el.eta.textContent = fmtEta(p.eta);
          if (p.filename) setStatus(`下載中：${p.filename}`);
        } else if (p.phase === 'finished_part') {
          setProgress(100);
          el.eta.textContent = '0 秒';
        }
        break;
      }
      case 'phase': {
        if (p.phase === 'processing') {
          setIndeterminate();
          el.speed.textContent = '—'; el.eta.textContent = '—';
          setStatus(p.text || '下載完成，正在合併／轉檔…');
        } else if (p.phase === 'extracting') {
          setIndeterminate();
          setStatus(p.text || '正在解析影片資訊…');
        }
        break;
      }
      case 'log': addLog(p.text, p.level); break;
      case 'done': {
        setBusy(false);
        setProgress(100);
        el.eta.textContent = '0 秒';
        if (p.size) el.size.textContent = fmtBytes(p.size);
        setStatus('下載完成！可按「開啟資料夾」查看影片。', 'ok');
        (p.paths || []).forEach((x) => addLog(`已儲存：${x}`, 'ok'));
        break;
      }
      case 'cancelled': {
        setBusy(false); setProgress(0);
        setStatus('已取消下載。', 'warn');
        addLog('已取消。', 'warn');
        break;
      }
      case 'error': {
        setBusy(false); setProgress(0);
        setStatus(`下載失敗：${p.message || '未知錯誤'}`, 'err');
        addLog(p.detail || p.message || '未知錯誤', 'error');
        if (p.hint) addLog(p.hint, 'warn');
        break;
      }
      case 'core_updated': {
        setUpdating(false);
        el.ver.textContent = `yt-dlp ${p.version}`;
        if (p.changed) { setStatus(`下載核心已更新到 ${p.version}，重新開啟程式後生效。`, 'ok'); addLog(`yt-dlp 已更新：${p.old} → ${p.version}（重新開啟程式後生效）`, 'ok'); }
        else { setStatus(`下載核心已是最新版（${p.version}）。`, 'ok'); addLog(`yt-dlp ${p.version} 已是最新版。`, 'ok'); }
        break;
      }
      case 'core_update_failed': {
        setUpdating(false);
        setStatus(`更新失敗：${p.message || ''}`, 'err');
        addLog(p.message || '更新失敗', 'error');
        break;
      }
      default: break;
    }
  };

  /* ───────── init ───────── */
  async function init() {
    if (state.inited && !(window.pywebview && window.pywebview.__mock === false)) { /* re-init only when real api replaces mock */ }
    state.inited = true;
    let s;
    try { s = await api().get_state(); }
    catch (e) { setStatus('無法連線到程式核心。', 'err'); return; }
    state.formats = s.formats || [];
    state.format = s.format || 'best';
    state.saveDir = s.save_dir || '';
    renderFormats();
    renderPath();
    el.ver.textContent = `yt-dlp ${s.ytdlp_version || '?'}`;
    el.foot.innerHTML = `${s.app || '萬能影片下載器'}<span class="dot">·</span>yt-dlp ${s.ytdlp_version || '?'}<span class="dot">·</span>ffmpeg ${s.ffmpeg ? '✓' : '✗ 未找到'}<span class="dot">·</span>JS ${s.js_runtime ? s.js_runtime + ' ✓' : '✗ 未安裝'}${window.pywebview.__mock ? '<span class="dot">·</span>預覽模式' : ''}`;
    el.log.innerHTML = '';
    addLog(`準備就緒 · yt-dlp ${s.ytdlp_version || '?'} · ffmpeg ${s.ffmpeg ? '可用' : '未找到（將無法合併高畫質影音／轉 MP3）'} · JS 執行環境 ${s.js_runtime || '未安裝'}`, s.ffmpeg ? 'ok' : 'warn');
    if (!s.ffmpeg) setStatus('找不到 ffmpeg，請按「更新下載核心」或把 ffmpeg.exe 放進 bin 資料夾。', 'warn');
    else if (!s.js_runtime) setStatus('尚未安裝 JavaScript 執行環境，YouTube 可能只有低畫質；按「更新下載核心」會自動安裝 deno。', 'warn');
  }

  el.paste.addEventListener('click', doPaste);
  el.start.addEventListener('click', doStart);
  el.cancel.addEventListener('click', doCancel);
  el.change.addEventListener('click', doChange);
  el.open.addEventListener('click', doOpen);
  el.update.addEventListener('click', doUpdate);
  el.url.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !state.busy) doStart(); });
  el.fmtBtn.addEventListener('click', (e) => { e.stopPropagation(); el.fmtSelect.classList.toggle('open'); });
  document.addEventListener('click', () => el.fmtSelect.classList.remove('open'));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') el.fmtSelect.classList.remove('open'); });

  buildClock();
  tickClock();
  setInterval(tickClock, 1000);

  /* pywebview 就緒後初始化；若在一般瀏覽器開啟（無 pywebview），改用模擬 API 供預覽。 */
  if (window.pywebview && window.pywebview.api) init();
  else {
    window.addEventListener('pywebviewready', init);
    setTimeout(() => { if (!window.pywebview) { installMock(); init(); } }, 1200);
  }

  /* ───────── 模擬 API（僅供瀏覽器預覽版面） ───────── */
  function installMock() {
    let timers = [];
    const push = (e, p) => window.__push(e, p);
    const clear = () => { timers.forEach(clearTimeout); timers = []; };
    const mock = {
      get_state: async () => ({
        app: '萬能影片下載器', save_dir: 'C:\\Users\\shinn\\Downloads\\下載影片', format: '1080', ytdlp_version: '2026.08.19', ffmpeg: true, js_runtime: 'deno',
        formats: [
          { key: 'best', label: 'MP4 影片・最高畫質' }, { key: '1080', label: 'MP4 影片・1080p 以下' },
          { key: '720', label: 'MP4 影片・720p 以下' }, { key: 'mp3', label: 'MP3 音訊' },
        ],
      }),
      read_clipboard: async () => 'https://www.facebook.com/reel/1380110447070009',
      choose_folder: async () => 'D:\\Videos\\Reels',
      open_folder: async () => true,
      set_format: async () => true,
      cancel_download: async () => { clear(); push('cancelled'); },
      update_core: async () => { timers.push(setTimeout(() => push('core_updated', { version: '2026.08.19', old: '2026.08.19', changed: false }), 1500)); return { ok: true }; },
      start_download: async (url) => {
        const total = 48_300_000, name = '241K views · 8K reactions｜歐告與嗎逼的🍰蛋糕開幕 [1380110447070009].mp4';
        timers.push(setTimeout(() => push('log', { text: `[facebook] Extracting URL: ${url}` }), 300));
        timers.push(setTimeout(() => push('log', { text: `[download] Destination: ${name}` }), 900));
        for (let i = 1; i <= 40; i++) {
          timers.push(setTimeout(() => push('progress', {
            phase: 'downloading', percent: i * 2.5, downloaded: total * i / 40, total,
            speed: 9_500_000 + Math.sin(i) * 2_000_000, eta: (40 - i) * 0.35, filename: name,
          }), 900 + i * 140));
        }
        timers.push(setTimeout(() => push('phase', { phase: 'processing' }), 900 + 41 * 140));
        timers.push(setTimeout(() => push('done', { paths: [`C:\\Users\\shinn\\Downloads\\下載影片\\${name}`], size: total }), 900 + 41 * 140 + 1400));
        return { ok: true };
      },
    };
    window.pywebview = { api: mock, __mock: true };
  }
})();
