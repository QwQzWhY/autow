const STATUS_URL = 'data/qq-status.json';
const API = 'https://api.github.com/repos';
const LS_REPO = 'autow.qq.repo';
const LS_TOKEN = 'autow.qq.token';

const $ = (id) => document.getElementById(id);

const settings = {
  repo: localStorage.getItem(LS_REPO) || '',
  token: localStorage.getItem(LS_TOKEN) || '',
};

// ------------------------------------------------------------------ helpers

function relTime(iso) {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return mins + ' 分钟前';
  const hours = Math.round(mins / 60);
  if (hours < 24) return hours + ' 小时前';
  return Math.round(hours / 24) + ' 天前';
}

function clock(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function copy(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Clipboard API needs a secure context; fall back to a manual selection.
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  const original = button.textContent;
  button.textContent = '已复制';
  setTimeout(() => { button.textContent = original; }, 1200);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text; // always text, never HTML
  return node;
}

// -------------------------------------------------------------- config view

function payloadTemplate() {
  const base = {
    event_type: 'qq-message',
    client_payload: {
      // MacroDroid magic text uses braces; these names come from its docs.
      group: '{not_title}',
      sender: '',
      text: '{notification}',
      time: '[year]-[month]-[day]T[hour]:[minute]',
    },
  };
  // The timestamp is left out on purpose: MacroDroid's date magic text is not
  // documented well enough to rely on, and the cloud stamps its own clock on
  // arrival instead.
  delete base.client_payload.time;
  return JSON.stringify(base);
}

function renderConfig() {
  const box = $('config');
  box.textContent = '';

  if (!settings.repo) {
    box.appendChild(el('p', 'hint', '填好上面的仓库名，这里就会生成可以直接复制的配置。'));
    return;
  }

  const rows = [
    ['请求地址', `${API}/${settings.repo}/dispatches`],
    ['Authorization', `Bearer ${settings.token || '你的令牌'}`],
    ['Accept', 'application/vnd.github+json'],
    ['Content-Type', 'application/json'],
    ['Body', payloadTemplate()],
  ];

  for (const [label, value] of rows) {
    const row = el('div', 'kv__row');
    const head = el('div', 'kv__head');
    head.appendChild(el('span', 'kv__label', label));
    const btn = el('button', 'kv__copy', '复制');
    btn.addEventListener('click', () => copy(value, btn));
    head.appendChild(btn);
    row.appendChild(head);
    row.appendChild(el('code', 'kv__value', value));
    box.appendChild(row);
  }

  if (!settings.token) {
    box.appendChild(el('p', 'hint', '填上令牌后，上面的 Authorization 才是完整可用的。'));
  }
}

// -------------------------------------------------------------- status view

function renderStatus(data) {
  const dot = $('status-dot');
  const connected = Boolean(data.connected);
  dot.className = 'dot ' + (connected ? 'dot--on' : 'dot--off');
  $('status-title').textContent = connected ? '已接通' : '还没有收到过消息';
  $('status-sub').textContent = connected
    ? `最后一条 ${relTime(data.recent[0]?.at || data.generated_at)}`
    : '按下面的三步配好之后，从手机发一条消息来试试。';

  const stats = $('stats');
  stats.textContent = '';
  const cells = [
    ['今日收到', data.today_received],
    ['今日待办', data.today_todos],
    ['累计收到', data.total_received],
    ['累计待办', data.total_todos],
  ];
  for (const [label, value] of cells) {
    const cell = el('div', 'stat');
    cell.appendChild(el('span', 'stat__value', String(value)));
    cell.appendChild(el('span', 'stat__label', label));
    stats.appendChild(cell);
  }

  const list = $('log');
  list.textContent = '';
  if (!data.recent || !data.recent.length) {
    list.appendChild(el('li', 'log__empty', '还没有收到过消息'));
    return;
  }
  for (const item of data.recent) {
    const li = el('li', 'log__item');
    li.appendChild(el('span', 'log__time', clock(item.at)));
    li.appendChild(el('span', `log__verdict log__verdict--${item.verdict}`, item.verdict_label));
    if (item.title) li.appendChild(el('span', 'log__title', item.title));
    list.appendChild(li);
  }
}

async function loadStatus() {
  try {
    const res = await fetch(STATUS_URL + '?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    renderStatus(data);
    $('meta').textContent = `数据更新于 ${relTime(data.generated_at)}`;
    return data;
  } catch (err) {
    $('status-title').textContent = '读不到状态文件';
    $('status-sub').textContent =
      '先在 Actions 里跑一次「扫描通知」，生成 data/qq-status.json。';
    $('meta').textContent = '加载失败';
    return null;
  }
}

// ------------------------------------------------------------------- actions

function saveSettings() {
  settings.repo = $('repo').value.trim().replace(/^https?:\/\/github\.com\//, '');
  settings.token = $('token').value.trim();
  localStorage.setItem(LS_REPO, settings.repo);
  localStorage.setItem(LS_TOKEN, settings.token);
  renderConfig();
}

// Two probes on purpose. The first proves the phone can reach GitHub; the
// second proves the model turns a chat message into a todo, which the first
// cannot show because it is worded so that no action is required.
const PLAIN_PROBE = '【AutoW 连接测试】看到这条说明手机已经能把消息送到云端了，无需处理。';
const TODO_PROBE = '【AutoW 测试】《人工智能导论》课程报告请于明天下午 6 点前提交到课程平台。';

async function sendTest(text) {
  const out = $('test-result');
  if (!settings.repo || !settings.token) {
    out.textContent = '先把仓库和令牌填好并保存。';
    out.className = 'result result--bad';
    return;
  }
  if (!settings.repo.includes('/')) {
    out.textContent = '仓库要写成 用户名/仓库名 的形式。';
    out.className = 'result result--bad';
    return;
  }

  const nonce = Math.random().toString(36).slice(2, 10);
  const body = {
    event_type: 'qq-message',
    client_payload: {
      group: 'AutoW 连接测试',
      sender: 'AutoW',
      text,
      nonce,
    },
  };

  out.className = 'result';
  out.textContent = '发送中…';

  let res;
  try {
    res = await fetch(`${API}/${settings.repo}/dispatches`, {
      method: 'POST',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${settings.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
  } catch (err) {
    out.textContent = `发送失败：${err.message}`;
    out.className = 'result result--bad';
    return;
  }

  if (res.status === 401 || res.status === 403) {
    out.textContent = '令牌被拒绝了。检查它对不对、有没有 Contents 读写权限、有没有过期。';
    out.className = 'result result--bad';
    return;
  }
  if (res.status === 404) {
    out.textContent = '找不到这个仓库。检查用户名/仓库名拼写，以及令牌有没有包含这个仓库。';
    out.className = 'result result--bad';
    return;
  }
  if (!res.ok) {
    out.textContent = `GitHub 返回 ${res.status}：${(await res.text()).slice(0, 160)}`;
    out.className = 'result result--bad';
    return;
  }

  out.textContent = '已发出，等待云端处理…';
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 4000));
    const data = await loadStatus();
    const hit = data?.recent?.find((item) => item.nonce === nonce);
    if (hit) {
      if (hit.verdict === 'todo') {
        out.textContent = `已生成待办《${hit.title}》，去待办页看看。`;
        out.className = 'result result--good';
      } else if (hit.verdict === 'no-model') {
        out.textContent = '云端收到了，但没配 LLM_API_KEY，不会生成待办。';
        out.className = 'result result--bad';
      } else {
        out.textContent = `云端已收到（${hit.verdict_label}）。链路是通的。`;
        out.className = 'result result--good';
      }
      return;
    }
    out.textContent = `已发出，等待云端处理…（第 ${i + 1} 次查询）`;
  }
  out.textContent =
    '等了两分钟还没看到。去 GitHub 的 Actions 页面看「处理 QQ 群消息」这次运行有没有报错。';
  out.className = 'result result--bad';
}

// --------------------------------------------------------------------- init

$('repo').value = settings.repo;
$('token').value = settings.token;
$('save').addEventListener('click', saveSettings);
$('test').addEventListener('click', () => sendTest(PLAIN_PROBE));
$('test-todo').addEventListener('click', () => sendTest(TODO_PROBE));

$('mode').addEventListener('click', (e) => {
  const btn = e.target.closest('.seg__item');
  if (!btn) return;
  document.querySelectorAll('.seg__item').forEach((b) =>
    b.classList.toggle('is-active', b === btn)
  );
  $('macro-guide').hidden = btn.dataset.mode !== 'macro';
  $('apk-guide').hidden = btn.dataset.mode !== 'apk';
});

renderConfig();
loadStatus();
