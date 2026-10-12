const DATA_URL = 'data/todos.json';
const DONE_KEY = 'autow.done';
const VIEW_KEY = 'autow.view';

// QQ 群消息是事件驱动的，云端处理完就写进 todos.json。页面开着的时候定期
// 拉一次，不用手动点刷新也能看到新待办；切回前台时立刻拉一次。
const REFRESH_MS = 60_000;

const state = {
  todos: [],
  generatedAt: null,
  done: new Set(JSON.parse(localStorage.getItem(DONE_KEY) || '[]')),
  view: localStorage.getItem(VIEW_KEY) || 'open',
};

const $ = (id) => document.getElementById(id);

function saveDone() {
  localStorage.setItem(DONE_KEY, JSON.stringify([...state.done]));
}

function dayDiff(iso) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(iso.slice(0, 10) + 'T00:00:00');
  return Math.round((target - today) / 86400000);
}

function dueLabel(iso) {
  if (!iso) return { text: '无截止日期', cls: '' };
  const days = dayDiff(iso);
  const time = iso.slice(11, 16);
  const at = time && time !== '23:59' ? ` ${time}` : '';
  if (days < 0) return { text: `已过期 ${-days} 天`, cls: 'due--overdue' };
  if (days === 0) return { text: `今天${at}`, cls: 'due--soon' };
  if (days === 1) return { text: `明天${at}`, cls: 'due--soon' };
  if (days <= 3) return { text: `${days} 天后${at}`, cls: 'due--soon' };
  return { text: `${iso.slice(5, 10)}${at}（${days} 天后）`, cls: '' };
}

function visible() {
  const items = state.todos.map((t) => ({ ...t, done: state.done.has(t.key) }));
  switch (state.view) {
    case 'done': return items.filter((t) => t.done);
    case 'all': return items;
    case 'soon': return items.filter((t) => !t.done && t.due_at && dayDiff(t.due_at) <= 3);
    default: return items.filter((t) => !t.done);
  }
}

function card(item) {
  const el = document.createElement('article');
  el.className = 'card';
  el.dataset.priority = item.priority;
  el.dataset.done = String(item.done);

  const due = dueLabel(item.due_at);
  const chipClass = item.source === 'qq' ? 'chip chip--qq' : 'chip';

  el.innerHTML = `
    <input class="check" type="checkbox" ${item.done ? 'checked' : ''} aria-label="完成">
    <div>
      <p class="card__title"></p>
      <div class="card__row">
        <span class="due ${due.cls}"></span>
        <span class="${chipClass}"></span>
        ${item.published_at ? `<span>${item.published_at.slice(5)} 发布</span>` : ''}
      </div>
      <p class="card__detail"></p>
      <button class="card__toggle">详情</button>
    </div>`;

  el.querySelector('.card__title').textContent = item.title;
  el.querySelector('.due').textContent = due.text;
  el.querySelector('.chip').textContent = item.source_label;
  const detail = el.querySelector('.card__detail');
  detail.textContent = item.detail || '（无补充说明）';

  const link = el.querySelector('.card__toggle');
  link.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = el.dataset.expanded === 'true';
    el.dataset.expanded = String(!open);
    link.textContent = open ? '详情' : '收起';
  });

  el.querySelector('.check').addEventListener('change', (e) => {
    if (e.target.checked) state.done.add(item.key); else state.done.delete(item.key);
    saveDone();
    render();
  });

  if (item.url) {
    const open = document.createElement('a');
    open.href = item.url;
    open.target = '_blank';
    open.rel = 'noopener';
    open.textContent = '打开原文 ↗';
    open.style.cssText = 'display:inline-block;margin-top:6px;font-size:12.5px';
    el.querySelector('div').appendChild(open);
  }
  return el;
}

function render() {
  const items = visible();
  const list = $('list');
  list.textContent = '';
  if (!items.length) {
    const p = document.createElement('p');
    p.className = 'empty';
    p.textContent = state.todos.length ? '这个分类下没有内容' : '还没有待办';
    list.appendChild(p);
  } else {
    items.forEach((item) => list.appendChild(card(item)));
  }

  const open = state.todos.filter((t) => !state.done.has(t.key)).length;
  $('meta').textContent = `${open} 项待办 · 共 ${state.todos.length} 条`;

  const when = state.generatedAt ? new Date(state.generatedAt).toLocaleString('zh-CN') : '未知';
  $('footer-note').textContent = `数据更新于 ${when}`;
}

async function load() {
  let data;
  try {
    const res = await fetch(DATA_URL + '?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    data = await res.json();
  } catch (err) {
    $('meta').textContent = '数据加载失败';
    $('list').innerHTML = `<p class="empty">读不到 data/todos.json（${err.message}）。<br>云端任务跑过之后才会有数据。</p>`;
    return;
  }
  // Skip the re-render when nothing changed, so an expanded card does not
  // collapse under the user every 60 seconds.
  const unchanged = data.generated_at && data.generated_at === state.generatedAt;
  state.todos = data.todos || [];
  state.generatedAt = data.generated_at;
  if (unchanged) return;
  render();
}

let pollTimer = null;

function schedulePoll() {
  clearInterval(pollTimer);
  if (document.visibilityState === 'visible') {
    pollTimer = setInterval(load, REFRESH_MS);
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') load();
  schedulePoll();
});

window.addEventListener('online', () => load());

$('filters').addEventListener('click', (e) => {
  const btn = e.target.closest('.filter');
  if (!btn) return;
  state.view = btn.dataset.view;
  localStorage.setItem(VIEW_KEY, state.view);
  document.querySelectorAll('.filter').forEach((b) =>
    b.classList.toggle('is-active', b === btn)
  );
  render();
});

$('refresh').addEventListener('click', () => load());

document.querySelectorAll('.filter').forEach((b) =>
  b.classList.toggle('is-active', b.dataset.view === state.view)
);

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

load();
schedulePoll();
