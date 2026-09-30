(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g,
    char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  const state = { logged: false, uname: '', offline: false };
  const downloads = [];
  let langMode = 'zh';
  let pollTimer = null;

  function toast(message, type = 'info') {
    const item = document.createElement('div');
    item.className = `toast ${type}`;
    item.innerHTML = `<span class="ti">${type === 'ok' ? '✓' : type === 'err' ? '!' : 'i'}</span><span>${esc(message)}</span>`;
    $('toasts').appendChild(item);
    setTimeout(() => item.remove(), 4200);
  }

  function renderAccount() {
    const area = $('acctArea');
    if (state.logged) {
      area.innerHTML = `<button class="acct-chip" id="acctChip" title="点击可重新扫码登录"><span class="av">${esc(state.uname.charAt(0) || 'B')}</span><span class="nm">${esc(state.uname)}<small>云端会话</small></span></button>`;
      $('acctChip').onclick = openLogin;
      $('acctInfo').innerHTML = `当前登录 <b>${esc(state.uname)}</b>。云端会话保留 30 天，之后请重新扫码。`;
      $('reloginText').textContent = '重新扫码登录';
    } else if (state.offline) {
      area.innerHTML = '<span class="pill warn"><span class="d"></span><span>登录状态暂时无法校验</span></span>';
      $('acctInfo').textContent = 'B站接口暂时无法校验登录状态，请稍后刷新或重新扫码。';
      $('reloginText').textContent = '重新扫码登录';
    } else {
      area.innerHTML = '<button class="login-top" id="loginTopBtn">扫码登录</button>';
      $('loginTopBtn').onclick = openLogin;
      $('acctInfo').textContent = '云端版本需要单独扫码登录 B站。不会读取电脑上的本地凭证。';
      $('reloginText').textContent = '扫码登录';
    }
  }

  async function loadStatus() {
    try {
      const response = await fetch('/api/status', { cache: 'no-store' });
      const data = await response.json();
      state.logged = !!data.logged_in;
      state.offline = !!data.offline;
      state.uname = data.uname || '';
      renderAccount();
    } catch {
      $('svcPill').classList.add('warn');
      $('svcPill').innerHTML = '<span class="d"></span><span>云端服务暂不可用</span>';
    }
  }

  function closeLogin() {
    $('loginMask').classList.remove('show');
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  function setScan(kind, label) {
    $('scanState').className = `scan-state ${kind}`;
    $('scanText').textContent = label;
  }

  async function startQr() {
    const box = $('qrBox');
    box.className = 'qrbox';
    box.textContent = '';
    setScan('cyan', '正在生成二维码…');
    try {
      const response = await fetch('/api/login/start', { method: 'POST' });
      const data = await response.json();
      if (!response.ok || !data.qr_url) throw new Error(data.error || '二维码生成失败');
      const qr = qrcode(0, 'M');
      qr.addData(data.qr_url);
      qr.make();
      box.innerHTML = qr.createSvgTag({ cellSize: 5, margin: 0 });
      setScan('cyan', '请用手机 B站 App 扫码并确认');
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(pollLogin, 2000);
    } catch (error) {
      setScan('gray', `二维码生成失败：${error.message}`);
    }
  }

  async function pollLogin() {
    try {
      const response = await fetch('/api/login/poll', { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '扫码状态查询失败');
      if (data.state === 'scanned') setScan('green', '已扫码，请在手机上确认');
      else if (data.state === 'success') {
        closeLogin();
        toast('登录成功', 'ok');
        loadStatus();
      } else if (data.state === 'expired') {
        clearInterval(pollTimer); pollTimer = null;
        $('qrBox').className = 'qrbox expired';
        $('qrBox').innerHTML = '<span>二维码已失效</span><button type="button" class="mini-btn" id="qrRetry">重新获取二维码</button>';
        $('qrRetry').onclick = startQr;
        setScan('gray', '二维码已失效');
      }
    } catch (error) {
      clearInterval(pollTimer); pollTimer = null;
      setScan('gray', error.message);
    }
  }

  function openLogin() {
    $('loginTitle').textContent = state.logged ? '重新扫码登录 B站' : '扫码登录 B站账号';
    $('loginSub').textContent = '首次使用云端版需重新扫码。登录凭证加密保存在浏览器会话中。';
    $('loginMask').classList.add('show');
    startQr();
  }

  document.querySelectorAll('#fmtBox .tag').forEach(button => {
    button.onclick = () => {
      button.classList.toggle('on');
      button.setAttribute('aria-pressed', String(button.classList.contains('on')));
    };
  });
  document.querySelectorAll('#langSeg button').forEach(button => {
    button.onclick = () => {
      document.querySelectorAll('#langSeg button').forEach(item => {
        item.classList.remove('on'); item.setAttribute('aria-pressed', 'false');
      });
      button.classList.add('on'); button.setAttribute('aria-pressed', 'true');
      langMode = button.dataset.l;
    };
  });

  function countUrls() {
    const urls = $('urls').value.match(/https?:\/\/[^\s,，;；。！？!?、）)】」』》"'’”…]+/g) || [];
    $('urlCnt').textContent = urls.length ? `已识别 ${urls.length} 个链接` : '';
  }
  $('urls').addEventListener('input', countUrls);
  $('exampleBtn').onclick = () => {
    $('urls').value = 'https://www.bilibili.com/video/BV11gYd6UEPv/';
    countUrls();
  };
  $('reloginBtn').onclick = openLogin;
  $('loginCancel').onclick = closeLogin;
  $('loginMask').onclick = event => { if (event.target === $('loginMask')) closeLogin(); };

  function fileLink(file) {
    const mime = file.format === 'md' ? 'text/markdown' :
      file.format === 'json' ? 'application/json' : 'text/plain';
    const contents = file.format === 'srt' ? `\uFEFF${file.content}` : file.content;
    const url = URL.createObjectURL(new Blob([contents], { type: `${mime};charset=utf-8` }));
    downloads.push(url);
    return `<a class="file" href="${url}" download="${esc(file.name)}"><span class="ft ${esc(file.format)}">${esc(file.format.toUpperCase())}</span><span class="fn" title="${esc(file.name)}">${esc(file.name)}</span><span class="dl">下载</span></a>`;
  }

  function renderResults(results) {
    downloads.splice(0).forEach(url => URL.revokeObjectURL(url));
    let success = 0, tracks = 0, files = 0;
    const parts = [];
    for (const result of results) {
      if (result.status === 'ok') success++;
      tracks += result.tracks || 0;
      files += result.files?.length || 0;
      const title = esc(result.title || result.url || '未知视频');
      const label = result.status === 'ok' ? '成功' : result.status === 'no_subtitle' ? '无所选语言字幕' : '失败';
      const badge = result.status === 'ok' ? 'ok-badge' : result.status === 'no_subtitle' ? 'warn-badge' : 'err-badge';
      parts.push(`<div class="video"><div class="video-hd"><div class="vt"><div class="t"><span class="${badge}" style="margin-right:8px">${label}</span>${title}</div><div class="m">${esc(result.bvid || '')}</div></div></div>${result.files?.length ? `<div class="file-grid">${result.files.map(fileLink).join('')}</div>` : `<div class="sub-msg">${esc(result.message || '未获取到所选语言的字幕，可稍后重试。')}</div>`}</div>`);
    }
    $('resSummary').innerHTML = `<span class="${success ? 'ok-badge' : 'warn-badge'}">${success}/${results.length} 个视频成功 · ${tracks} 条字幕轨 · ${files} 个文件</span>`;
    $('resBody').innerHTML = parts.join('');
    $('resCard').style.display = 'block';
    if (success) toast(`提取完成，共 ${files} 个文件`, 'ok');
  }

  async function startExtract() {
    const urls = $('urls').value.trim();
    if (!urls) { toast('请先粘贴视频链接', 'err'); $('urls').focus(); return; }
    const fmts = [...document.querySelectorAll('#fmtBox .tag.on')].map(button => button.dataset.f);
    if (!fmts.length) { toast('至少选择一种导出格式', 'err'); return; }
    $('goBtn').disabled = true;
    $('goText').textContent = '正在提取…';
    $('runBox').style.display = 'block';
    $('resCard').style.display = 'none';
    $('runMsg').textContent = '正在从 B站获取字幕，请保持页面打开';
    $('log').textContent = '云端提取中…\n';
    $('progBar').style.width = '35%';
    $('pctText').textContent = '处理中';
    try {
      const response = await fetch('/api/extract', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ urls, fmts, pages: $('pages').value || 'all', langs: [langMode] }),
      });
      const data = await response.json();
      if (response.status === 401) { openLogin(); throw new Error('请先扫码登录'); }
      if (!response.ok) throw new Error(data.error || '提取失败');
      $('progBar').style.width = '100%'; $('pctText').textContent = '100%';
      $('runMsg').textContent = '提取完成';
      $('log').textContent = (data.results || []).map(result =>
        `${result.title || result.url}: ${result.status === 'ok' ? `${result.files.length} 个文件` : result.message || '无字幕'}`).join('\n');
      renderResults(data.results || []);
    } catch (error) {
      $('runBox').style.display = 'none';
      toast(error.message, 'err');
    } finally {
      $('goBtn').disabled = false;
      $('goText').textContent = '开始提取';
    }
  }

  $('goBtn').onclick = startExtract;
  $('ver').textContent = '云端版';
  $('svcPill').innerHTML = '<span class="d"></span><span>私有云端站点</span>';
  $('outDirInfo').textContent = '文件在浏览器中生成并下载；云端不会永久保存导出的字幕。';
  $('folderBtn').style.display = 'none';
  $('openOutBtn').style.display = 'none';
  loadStatus();
})();
