// Pure view logic for the todo board.
//
// Kept in a module with no DOM access so it can be tested with `node --test`
// instead of only by looking at the page.

/** Midnight of `date`, so two timestamps on the same day compare equal. */
function startOfDay(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  return d;
}

/** Whole days from today until the date part of `iso`. Negative means overdue. */
export function dayDiff(iso, today = new Date()) {
  const target = startOfDay(new Date(`${iso.slice(0, 10)}T00:00:00`));
  return Math.round((target - startOfDay(today)) / 86400000);
}

/** How a deadline should read, and whether to colour it. */
export function dueLabel(iso, today = new Date()) {
  if (!iso) return { text: '无截止日期', cls: '' };

  const days = dayDiff(iso, today);
  const time = iso.slice(11, 16);
  // 23:59 is our stand-in for "end of that day", not a real time to show.
  const at = time && time !== '23:59' ? ` ${time}` : '';

  if (days < 0) return { text: `已过期 ${-days} 天`, cls: 'due--overdue' };
  if (days === 0) return { text: `今天${at}`, cls: 'due--soon' };
  if (days === 1) return { text: `明天${at}`, cls: 'due--soon' };
  if (days <= 3) return { text: `${days} 天后${at}`, cls: 'due--soon' };
  return { text: `${iso.slice(5, 10)}${at}（${days} 天后）`, cls: '' };
}

/**
 * Whether one card is already handled.
 *
 * Two sources on purpose. `status` comes back from the cloud once the board has
 * reported a tick, so it is what a second device sees; `done` is this device's
 * own set, which covers the minute or two before the cloud has run.
 */
export function isDone(item, done = new Set()) {
  return item.status === 'done' || done.has(item.key);
}

/**
 * The cards to show, given what the user has already handled.
 *
 * `done` and `deleted` are sets of keys kept in localStorage.
 */
export function filterTodos(todos, options = {}) {
  const {
    view = 'open',
    done = new Set(),
    deleted = new Set(),
    today = new Date(),
  } = options;

  const items = todos
    .filter((t) => !deleted.has(t.key))
    .map((t) => ({ ...t, done: isDone(t, done) }));

  switch (view) {
    case 'done':
      return items.filter((t) => t.done);
    case 'all':
      return items;
    case 'soon':
      return items.filter((t) => !t.done && t.due_at && dayDiff(t.due_at, today) <= 3);
    default:
      return items.filter((t) => !t.done);
  }
}

/**
 * Split cards into "just go and do it" and "someone has to nominate you first".
 *
 * The second kind is still worth seeing — it just is not something the reader
 * can act on alone, so it is shown as its own group rather than mixed in.
 */
export function splitByNomination(items) {
  const self = [];
  const nomination = [];
  for (const item of items) {
    (item.needs_recommendation ? nomination : self).push(item);
  }
  return { self, nomination };
}

/**
 * Split cards into "has a deadline" and "just worth knowing".
 *
 * A notice with no deadline is usually an activity, a ceremony or a workshop.
 * Keeping those is deliberate — the user asked for optional-but-useful things
 * to survive filtering — but they are not the same kind of thing as something
 * due tomorrow, and mixing them makes the board read as one long list of equal
 * demands.
 */
export function splitByDeadline(items) {
  const dated = [];
  const undated = [];
  for (const item of items) {
    (item.due_at ? dated : undated).push(item);
  }
  return { dated, undated };
}
