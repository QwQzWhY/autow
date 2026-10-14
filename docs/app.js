// The version query has to match the other assets: a fresh app.js loading a
// cached older todo-view.js fails at runtime, not with a warning.
import {
  dueLabel,
  filterTodos,
  isDone,
  splitByDeadline,
  splitByNomination,
} from './todo-view.js?v=9';

const DATA_URL = 'data/todos.json';
const DONE_KEY = 'autow.done';
const DELETED_KEY = 'autow.deleted';
const VIEW_KEY = 'autow.view';
const PENDING_KEY = 'autow.pending';

// The repo and token the user typed on the QQ setup page; same origin, so the
// board can reuse them to submit a new todo.
const LS_REPO = 'autow.qq.repo';
const LS_TOKEN = 'autow.qq.token';
const DISPATCH_URL = 'https://api.github.com/repos';

// Past this, a submitted item that never turned up is flagged rather than kept
// as if it were still on its way. It is not deleted: silently losing something
// the user typed is worse than showing it in a warning colour.
const PENDING_GIVE_UP_MS = 5 * 60 * 1000;

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

const readJson = (key, fallback) => {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value == null ? fallback : value;
  } catch {
    return fallback;
  }
};

const state = {
  todos: [],
  generatedAt: null,
  done: readSet(DONE_KEY),
  deleted: readSet(DELETED_KEY),
  view: localStorage.getItem(VIEW_KEY) || 'open',
  undo: null, // { key, title } for the item just deleted
  pending: readJson(PENDING_KEY, []), // submitted, not yet visible in the cloud
};

const $ = (id) => document.getElementById(id);

function saveDone() {
  localStorage.setItem(DONE_KEY, JSON.stringify([...state.done]));
}

function saveDeleted() {
  localStorage.setItem(DELETED_KEY, JSON.stringify([...state.deleted]));
}

function savePending() {
  localStorage.setItem(PENDING_KEY, JSON.stringify(state.pending));
}

// ------------------------------------------------------- creating a new todo

/** Repo + token, as typed on the QQ setup page. */
function credentials() {
  return {
    repo: localStorage.getItem(LS_REPO) || '',
    token: localStorage.getItem(LS_TOKEN) || '',
  };
}

async function dispatch(payload) {
  const { repo, token } = credentials();
  if (!repo.includes('/') || !token) {
    throw new Error('还没配置仓库和令牌，先去 QQ 接入页填一次');
  }
  const res = await fetch(`${DISPATCH_URL}/${repo}/dispatches`, {
    method: 'POST',
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ event_type: 'qq-message', client_payload: payload }),
  });
  if (res.status === 401 || res.status === 403) {
    throw new Error('令牌被拒绝：检查权限（Contents 读写）和有效期');
  }
  if (res.status === 404) {
    throw new Error('找不到仓库：检查仓库名和令牌能访问的范围');
  }
  if (!res.ok) {
    throw new Error(`GitHub 返回 ${res.status}`);
  }
}

/**
 * Tell the cloud what the user did with a card.
 *
 * Ticking and deleting used to be written into this browser's localStorage and
 * nowhere else: a second device showed the item as open again, clearing site
 * data brought deleted items back, and the published file kept listing things
 * that were already done. Failures are swallowed on purpose — the local state
 * already shows what the user did, and there is no useful place to report a
 * background sync error.
 */
async function reportStatus(key, status) {
  try {
    await dispatch({ kind: 'status', key, status });
  } catch (err) {
    // Nothing to do: the tick stays on screen either way.
  }
}

/**
 * Show it immediately, then let the cloud take over.
 *
 * The cloud has to run the model and GitHub Pages has to republish before the
 * item comes back, which takes a minute or two — waiting behind a spinner for
 * that would feel broken.
 */
async function create(item, payload) {
  const out = $('compose-result');
  out.className = 'result';
  out.textContent = '提交中…';
  try {
    await dispatch(payload);
  } catch (err) {
    out.textContent = `提交失败：${err.message}`;
    out.className = 'result result--bad';
    return false;
  }
  state.pending.unshift({
    key: item.key,
    title: item.title || '',
    detail: item.detail || '',
    due_at: item.due_at ?? null,
    priority: item.priority || 'normal',
    source: 'self',
    source_label: item.label || '待识别',
    url: '',
    published_at: '',
    // Carried through so the split below puts a hand-added "要先经学院推荐"
    // item in its own group right away.
    needs_recommendation: Boolean(item.needs_recommendation),
    pending: true,
    at: Date.now(),
  });
  savePending();
  closeSheet();
  render();
  toast('已提交，识别完会自动出现在列表里');
  return true;
}

function closeSheet() {
  $('sheet').hidden = true;
}

/** The little heading that separates one group of cards from the next. */
function groupTitle(text) {
  const heading = document.createElement('p');
  heading.className = 'group-title';
  heading.textContent = text;
  return heading;
}

let toastTimer = null;

function toast(text) {
  $('toast').textContent = text;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4000);
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
  reportStatus(state.undo.key, 'open');
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
  let chipClass = item.source === 'qq' ? 'chip chip--qq' : 'chip';
  let chipText = item.source_label;
  if (item.pending) {
    const stuck = Date.now() - (item.at || 0) > PENDING_GIVE_UP_MS;
    chipClass = stuck ? 'chip chip--stuck' : 'chip chip--pending';
    chipText = stuck ? '未同步' : '同步中';
    el.dataset.pending = 'true';
  }

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
  chip.textContent = chipText;
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

  // A pending item is not on the server yet, so there is nothing to tick off.
  if (!item.pending) check.addEventListener('click', () => {
    const nowDone = check.getAttribute('aria-checked') !== 'true';
    check.setAttribute('aria-checked', String(nowDone));
    el.dataset.done = String(nowDone);
    // Let the tick draw before the card leaves.
    setTimeout(() => {
      if (nowDone) state.done.add(item.key);
      else state.done.delete(item.key);
      saveDone();
      collapse(el);
      reportStatus(item.key, nowDone ? 'done' : 'open');
    }, nowDone ? TICK_MS : 0);
  });

  el.querySelector('.card__delete').addEventListener('click', () => {
    state.deleted.add(item.key);
    saveDeleted();
    offerUndo(item);
    collapse(el);
    reportStatus(item.key, 'deleted');
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

  // Whatever was just typed shows up first, so the thing the user is waiting on
  // is the first thing he sees.
  const pending =
    state.view === 'done'
      ? []
      : state.pending.filter((p) => !state.deleted.has(p.key));

  const list = $('list');
  list.textContent = '';
  if (!items.length && !pending.length) {
    const p = document.createElement('p');
    p.className = 'empty';
    p.textContent = state.todos.length ? '这个分类下没有内容' : '还没有待办';
    list.appendChild(p);
  } else {
    // Pending cards go through the same split, so one the user just marked as
    // "要先经学院推荐" is already in that group instead of sitting at the top and
    // jumping down when the server copy arrives.
    const { self, nomination } = splitByNomination([...pending, ...items]);

    // What has a deadline goes first; an activity with no deadline is worth
    // keeping but is not the same kind of thing as something due tomorrow.
    const { dated, undated } = splitByDeadline(self);
    dated.forEach((item) => list.appendChild(card(item)));
    if (undated.length) {
      list.appendChild(groupTitle('没有截止时间'));
      undated.forEach((item) => list.appendChild(card(item)));
    }
    if (nomination.length) {
      list.appendChild(groupTitle('需学院推荐才能参加'));
      nomination.forEach((item) => list.appendChild(card(item)));
    }
  }

  // Counted the same way the cards are filtered: a tick the cloud already knows
  // about sits in `status`, not in this device's set.
  const handled = state.todos.filter((t) => isDone(t, state.done)).length;
  const hidden = state.todos.filter((t) => state.deleted.has(t.key)).length;
  $('meta').textContent =
    `${state.todos.length - handled - hidden + pending.length} 项待办 · ` +
    `共 ${state.todos.length} 条`;

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

  // Anything the cloud now knows about is no longer "pending"; the real card
  // replaces the optimistic one.
  const known = new Set(state.todos.map((t) => t.key));
  const stillPending = state.pending.filter((p) => !known.has(p.key));
  if (stillPending.length !== state.pending.length) {
    state.pending = stillPending;
    savePending();
  }

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

// ------------------------------------------------------------------ 新建面板

$('fab').addEventListener('click', () => {
  $('compose-result').textContent = '';
  $('sheet').hidden = false;
});

$('sheet-close').addEventListener('click', closeSheet);

$('sheet').addEventListener('click', (e) => {
  if (e.target === $('sheet')) closeSheet();
});

$('compose-mode').addEventListener('click', (e) => {
  const btn = e.target.closest('.seg__item');
  if (!btn) return;
  const manual = btn.dataset.mode === 'manual';
  document
    .querySelectorAll('#compose-mode .seg__item')
    .forEach((b) => b.classList.toggle('is-active', b === btn));
  $('note-form').hidden = manual;
  $('manual-form').hidden = !manual;
});

$('note-submit').addEventListener('click', async () => {
  const text = $('note-text').value.trim();
  const out = $('compose-result');
  if (!text) {
    out.textContent = '先把通知粘进来';
    out.className = 'result result--bad';
    return;
  }
  const id = Math.random().toString(36).slice(2, 10);
  const ok = await create(
    { key: `note:${id}`, label: '待识别', title: text.split('\n')[0].slice(0, 40) },
    { kind: 'note', id, text }
  );
  if (ok) $('note-text').value = '';
});

$('manual-submit').addEventListener('click', async () => {
  const title = $('manual-title').value.trim();
  const out = $('compose-result');
  if (!title) {
    out.textContent = '标题不能为空';
    out.className = 'result result--bad';
    return;
  }
  const due = $('manual-due').value;
  const priority = $('manual-priority').value;
  const detail = $('manual-detail').value.trim();
  const nomination = $('manual-nomination').checked;
  const id = Math.random().toString(36).slice(2, 10);
  const ok = await create(
    {
      key: `manual:${id}`,
      title,
      detail,
      due_at: due || null,
      priority,
      needs_recommendation: nomination,
      label: '手动',
    },
    { kind: 'manual', id, title, due_at: due, priority, detail, needs_recommendation: nomination }
  );
  if (ok) {
    $('manual-title').value = '';
    $('manual-due').value = '';
    $('manual-detail').value = '';
    $('manual-priority').value = 'normal';
    $('manual-nomination').checked = false;
  }
});

document
  .querySelectorAll('.filter')
  .forEach((b) => b.classList.toggle('is-active', b.dataset.view === state.view));

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}

load();
schedulePoll();
