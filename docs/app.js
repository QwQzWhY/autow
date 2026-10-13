import { dueLabel, filterTodos } from './todo-view.js';

const DATA_URL = 'data/todos.json';
const DONE_KEY = 'autow.done';
const DELETED_KEY = 'autow.deleted';
const VIEW_KEY = 'autow.view';

// QQ 群消息是事件驱动的，云端处理完就写进 todos.json。页面开着的时候定期
// 拉一次，不用手动点刷新也能看到新待办；切回前台时立刻拉一次。
const REFRESH_MS = 60_000;

// Tick first, then collapse. Long enough to read as an acknowledgement rather
// than the card just vanishing under the finger.
const TICK_MS = 320;
const COLLAPSE_MS = 420;

const readSet = (key) => {
  try {
    return new Set(JSON.parse(localStorage.getItem(key) || '[]'));
  } catch {
    return new Set();
  }
};

const state = {
  todos: [],
  generatedAt: null,
  done: readSet(DONE_KEY),
  deleted: readSet(DELETED_KEY),
  view: localStorage.getItem(VIEW_KEY) || 'open',
  undo: null, // { key, title } for the item just deleted
};

const $ = (id) => document.getElementById(id);

function saveDone() {
  localStorage.setItem(DONE_KEY, JSON.stringify([...state.done]));
}

function saveDeleted() {
  localStorage.setItem(DELETED_KEY, JSON.stringify([...state.deleted]));
}

// ------------------------------------------------------------------ undo bar

let undoTimer = null;

function offerUndo(item) {
  state.undo = { key: item.key, title: item.title };
  $('undo-text').textContent = `已删除「${item.title}」`;
  $('undo').hidden = false;
  clearTimeout(undoTimer);
  undoTimer = setTimeout(clearUndo, 6000);
}

function clearUndo() {
  state.undo = null;
  $('undo').hidden = true;
  clearTimeout(undoTimer);
}

function restoreUndo() {
  if (!state.undo) return;
  state.deleted.delete(state.undo.key);
  saveDeleted();
  clearUndo();
  render();
}

// ----------------------------------------------------------------- one card

function card(item) {
  const el = document.createElement('article');
  el.className = 'card';
  el.dataset.priority = item.priority;
  el.dataset.done = String(item.done);

  const due = dueLabel(item.due_at);
  const chipClass = item.source === 'qq' ? 'chip chip--qq' : 'chip';

  el.innerHTML = `
    <button class="check" type="button" role="checkbox" aria-checked="false"
            aria-label="标记完成">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>
    </button>
    <div class="card__main">
      <p class="card__title"></p>
      <div class="card__row">
        <span class="due"></span>
        <span class="chip-slot"></span>
        <span class="published"></span>
      </div>
      <p class="card__detail"></p>
      <button class="card__toggle" type="button">详情</button>
    </div>
    <button class="card__delete" type="button" aria-label="删除这条待办">×</button>`;

  const check = el.querySelector('.check');
  check.setAttribute('aria-checked', String(item.done));

  el.querySelector('.card__title').textContent = item.title;
  const dueEl = el.querySelector('.due');
  dueEl.className = `due ${due.cls}`;
  dueEl.textContent = due.text;
  const chip = el.querySelector('.chip-slot');
  chip.className = chipClass;
  chip.textContent = item.source_label;
  el.querySelector('.published').textContent = item.published_at
    ? `${item.published_at.slice(5)} 发布`
    : '';
  el.querySelector('.card__detail').textContent = item.detail || '（无补充说明）';

  el.querySelector('.card__toggle').addEventListener('click', (e) => {
    e.stopPropagation();
    const open = el.dataset.expanded === 'true';
    el.dataset.expanded = String(!open);
    e.target.textContent = open ? '详情' : '收起';
  });

  if (item.url) {
    const link = document.createElement('a');
    link.href = item.url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = '打开原文 ↗';
    link.className = 'card__link';
    el.querySelector('.card__main').appendChild(link);
  }

  check.addEventListener('click', () => {
    const nowDone = check.getAttribute('aria-checked') !== 'true';
    check.setAttribute('aria-checked', String(nowDone));
    el.dataset.done = String(nowDone);
    // Let the tick draw before the card leaves.
    setTimeout(() => {
      if (nowDone) state.done.add(item.key);
      else state.done.delete(item.key);
      saveDone();
      collapse(el);
    }, nowDone ? TICK_MS : 0);
  });

  el.querySelector('.card__delete').addEventListener('click', () => {
    state.deleted.add(item.key);
    saveDeleted();
    offerUndo(item);
    collapse(el);
  });

  return el;
}

/**
 * Shrink a card away, then re-render.
 *
 * The re-render rebuilds every card, so it has to wait for the animation —
 * otherwise the card vanishes the instant it is tapped and the tick is never
 * seen.
 */
function collapse(el) {
  // Measure before changing anything: height:auto cannot be animated to zero.
  el.style.height = `${el.offsetHeight}px`;
  requestAnimationFrame(() => el.classList.add('card--leaving'));
  setTimeout(() => {
    el.remove();
    render();
  }, COLLAPSE_MS);
}

// -------------------------------------------------------------------- render

function render() {
  const items = filterTodos(state.todos, {
    view: state.view,
    done: state.done,
    deleted: state.deleted,
  });

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

  const hidden = state.todos.filter((t) => state.deleted.has(t.key)).length;
  $('meta').textContent =
    `${state.todos.length - state.done.size - hidden} 项待办 · 共 ${state.todos.length} 条`;

  const when = state.generatedAt
    ? new Date(state.generatedAt).toLocaleString('zh-CN')
    : '未知';
  $('footer-note').textContent = `数据更新于 ${when}`;
}

// ---------------------------------------------------------------------- load

async function load() {
  let data;
  try {
    const res = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    $('meta').textContent = '数据加载失败';
    $('list').innerHTML =
      `<p class="empty">读不到 data/todos.json（${err.message}）。<br>云端任务跑过之后才会有数据。</p>`;
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
  document
    .querySelectorAll('.filter')
    .forEach((b) => b.classList.toggle('is-active', b === btn));
  render();
});

$('refresh').addEventListener('click', () => load());
$('undo-btn').addEventListener('click', restoreUndo);

document
  .querySelectorAll('.filter')
  .forEach((b) => b.classList.toggle('is-active', b.dataset.view === state.view));

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

load();
schedulePoll();
