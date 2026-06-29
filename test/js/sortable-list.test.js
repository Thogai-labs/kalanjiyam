import { $ } from '@/core.ts';
import SortableList from '@/sortable-list';

const sampleHTML = `
<ul>
  <li data-key="a" data-bar="3" data-title="Title A">A</li>
  <li data-key="b" data-bar="1" data-title="Title B">B</li>
  <li data-key="c" data-bar="2" data-title="Title C">C</li>
</ul>
`;

beforeEach(() => {
  document.write(sampleHTML);
});

function getText(list) {
  return [...list.children].map(x => x.textContent);
}

test('SortableList can be created', () => {
  const s = SortableList('key');
  s.$refs = { list: document.querySelector('ul') };
  s.init()

  expect(s.field).toBe('key');
  expect(s.order).toBe('asc');
  expect(s.displayed).toEqual(new Set(["a", "b", "c"]))
  expect(s.data).toEqual([
    { key: "a", title: "title a" },
    { key: "b", title: "title b" },
    { key: "c", title: "title c" },
  ]);

  const results = getText(s.$refs.list);
  expect(results).toEqual(['A', 'B', 'C']);
});

test('filter filters on a query', () => {
  const s = SortableList('key');
  s.$refs = { list: document.querySelector('ul') };
  s.init()

  s.query = "B";
  s.filter();
  expect(s.displayed).toEqual(new Set(["b"]))
});

test('filter is a no-op if the query is empty', () => {
  const s = SortableList('key');
  s.$refs = { list: document.querySelector('ul') };
  s.init()

  s.filter();
  expect(s.displayed).toEqual(new Set(["a", "b", "c"]))
});

test('filter filters on creator mode', () => {
  const testHTML = `
  <ul>
    <li data-key="1" data-title="A" data-mode="unregistered">A</li>
    <li data-key="2" data-title="B" data-mode="registered">B</li>
    <li data-key="3" data-title="C" data-mode="enterprise">C</li>
  </ul>
  `;
  document.body.innerHTML = testHTML;

  const s = SortableList('key');
  s.$refs = { list: document.querySelector('ul') };
  s.init();

  s.selectedMode = "unregistered";
  s.filter();
  expect(s.displayed).toEqual(new Set(["1"]));

  s.selectedMode = "registered";
  s.filter();
  expect(s.displayed).toEqual(new Set(["2"]));

  s.selectedMode = "enterprise";
  s.filter();
  expect(s.displayed).toEqual(new Set(["3"]));

  s.selectedMode = "all";
  s.filter();
  expect(s.displayed).toEqual(new Set(["1", "2", "3"]));
});

test('sort ascending', () => {
  const s = SortableList('key');
  s.$refs = { list: document.querySelector('ul') };

  s.field = 'bar';
  s.sort();

  const results = getText(s.$refs.list);
  expect(results).toEqual(['B', 'C', 'A']);
});


test('sort descending', () => {
  const s = SortableList('key');
  s.$refs = { list: document.querySelector('ul') };

  s.field = 'bar';
  s.order = 'desc'
  s.sort();

  const results = getText(s.$refs.list);
  expect(results).toEqual(['A', 'C', 'B']);
});
