// Browser-side logic, exercised without a browser:
//
//     node --test tests/js
//
// Kept in this file rather than in app.js because app.js only exists inside a
// page; todo-view.js holds the parts that are worth asserting.
import test from 'node:test';
import assert from 'node:assert/strict';

import { dayDiff, dueLabel, filterTodos } from '../../docs/todo-view.js';

const TODAY = new Date('2026-10-13T09:00:00');

test('dayDiff counts whole days from today', () => {
  assert.equal(dayDiff('2026-10-13T18:00', TODAY), 0);
  assert.equal(dayDiff('2026-10-14T00:00', TODAY), 1);
  assert.equal(dayDiff('2026-10-12T23:59', TODAY), -1);
  assert.equal(dayDiff('2026-11-20T23:59', TODAY), 38);
});

test('dueLabel names today, tomorrow and the days after', () => {
  assert.equal(dueLabel('2026-10-13T18:00', TODAY).text, '今天 18:00');
  assert.equal(dueLabel('2026-10-14T18:00', TODAY).text, '明天 18:00');
  assert.equal(dueLabel('2026-10-16T21:20', TODAY).text, '3 天后 21:20');
});

test('an end-of-day deadline does not display a fake time', () => {
  assert.equal(dueLabel('2026-10-16T23:59', TODAY).text, '3 天后');
});

test('overdue deadlines are labelled and coloured', () => {
  const label = dueLabel('2026-10-12T12:00', TODAY);
  assert.equal(label.text, '已过期 1 天');
  assert.equal(label.cls, 'due--overdue');
});

test('a missing deadline is its own label', () => {
  assert.equal(dueLabel(null, TODAY).text, '无截止日期');
});

test('a far deadline shows the date', () => {
  assert.equal(dueLabel('2026-11-20T23:59', TODAY).text, '11-20（38 天后）');
});

const TODOS = [
  { key: 'a', title: 'A', due_at: '2026-10-14T18:00' },
  { key: 'b', title: 'B', due_at: '2026-10-20T18:00' },
  { key: 'c', title: 'C', due_at: null },
];

test('the open view hides completed and deleted cards', () => {
  const out = filterTodos(TODOS, {
    done: new Set(['a']),
    deleted: new Set(['b']),
    today: TODAY,
  });
  assert.deepEqual(out.map((t) => t.key), ['c']);
});

test('the all view keeps everything that was not deleted', () => {
  const out = filterTodos(TODOS, {
    view: 'all',
    done: new Set(['a']),
    deleted: new Set(['b']),
    today: TODAY,
  });
  assert.deepEqual(out.map((t) => t.key), ['a', 'c']);
});

test('the done view shows only completed cards', () => {
  const out = filterTodos(TODOS, { view: 'done', done: new Set(['b']), today: TODAY });
  assert.deepEqual(out.map((t) => t.key), ['b']);
});

test('the soon view keeps only deadlines inside three days', () => {
  const out = filterTodos(TODOS, { view: 'soon', today: TODAY });
  assert.deepEqual(out.map((t) => t.key), ['a']);
});

test('a deleted card stays gone in every view', () => {
  for (const view of ['open', 'soon', 'done', 'all']) {
    const out = filterTodos(TODOS, {
      view,
      deleted: new Set(['a', 'b', 'c']),
      today: TODAY,
    });
    assert.deepEqual(out, [], `view=${view}`);
  }
});

test('completed state is reported per card, not baked into the data', () => {
  const out = filterTodos(TODOS, { done: new Set(['a']), today: TODAY });
  assert.equal(out.find((t) => t.key === 'a'), undefined); // hidden by the view
  const all = filterTodos(TODOS, { view: 'all', done: new Set(['a']), today: TODAY });
  assert.equal(all.find((t) => t.key === 'a').done, true);
  assert.equal(all.find((t) => t.key === 'c').done, false);
});
