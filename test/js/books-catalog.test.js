import booksCatalog from '@/books-catalog';

describe('booksCatalog Alpine.js component', () => {
  const sampleBooks = [
    {
      id: 1,
      title: 'Tirukkural',
      author: 'Thiruvalluvar',
      description: 'Classic Tamil literature on ethics and morality.',
      url: '/books/tirukkural/',
      stats: {
        total_pages: 133,
        ocr_pages: 133,
        ocr_percentage: 100.0,
        translated_pages: 133,
        translation_percentage: 100.0,
      },
    },
    {
      id: 2,
      title: 'Agastyar Vaidyam',
      author: 'Agastyar',
      description: 'Ancient Siddha medicine manuscript.',
      url: '/books/agastyar-vaidyam/',
      stats: {
        total_pages: 50,
        ocr_pages: 25,
        ocr_percentage: 50.0,
        translated_pages: 0,
        translation_percentage: 0.0,
      },
    },
    {
      id: 3,
      title: 'Bhagavad Gita',
      author: 'Vyasa',
      description: 'Sacred Hindu scripture.',
      url: '/books/bhagavad-gita/',
      stats: {
        total_pages: 700,
        ocr_pages: 0,
        ocr_percentage: 0.0,
        translated_pages: 0,
        translation_percentage: 0.0,
      },
    },
  ];

  test('initializes correctly with books and default query', () => {
    const catalog = booksCatalog(sampleBooks, 'siddha');
    catalog.init();
    expect(catalog.allBooks.length).toBe(3);
    expect(catalog.query).toBe('siddha');
    expect(catalog.searchQuery).toBe('siddha');
    expect(catalog.activeFilter).toBe('all');
    expect(catalog.page).toBe(1);
  });

  test('initializes correctly from DOM JSON script element', () => {
    const scriptEl = document.createElement('script');
    scriptEl.type = 'application/json';
    scriptEl.id = 'test-books-payload';
    scriptEl.textContent = JSON.stringify(sampleBooks);
    document.body.appendChild(scriptEl);

    const catalog = booksCatalog('test-books-payload', 'gita');
    catalog.init();
    expect(catalog.allBooks.length).toBe(3);
    expect(catalog.query).toBe('gita');

    document.body.removeChild(scriptEl);
  });

  test('filters books by title, author, or description', () => {
    const catalog = booksCatalog(sampleBooks);
    catalog.init();

    catalog.searchQuery = 'valluvar';
    let filtered = catalog.filteredBooks();
    expect(filtered.length).toBe(1);
    expect(filtered[0].title).toBe('Tirukkural');

    catalog.searchQuery = 'medicine';
    filtered = catalog.filteredBooks();
    expect(filtered.length).toBe(1);
    expect(filtered[0].title).toBe('Agastyar Vaidyam');

    catalog.searchQuery = 'non-existent-keyword';
    filtered = catalog.filteredBooks();
    expect(filtered.length).toBe(0);
  });

  test('filters books by status chip', () => {
    const catalog = booksCatalog(sampleBooks);
    catalog.init();

    catalog.setFilter('completed');
    expect(catalog.filteredBooks().length).toBe(1);
    expect(catalog.filteredBooks()[0].title).toBe('Tirukkural');

    catalog.setFilter('in_progress');
    expect(catalog.filteredBooks().length).toBe(1);
    expect(catalog.filteredBooks()[0].title).toBe('Agastyar Vaidyam');

    catalog.setFilter('translated');
    expect(catalog.filteredBooks().length).toBe(1);
    expect(catalog.filteredBooks()[0].title).toBe('Tirukkural');

    catalog.setFilter('all');
    expect(catalog.filteredBooks().length).toBe(3);
  });

  test('computes filter counts accurately', () => {
    const catalog = booksCatalog(sampleBooks);
    const counts = catalog.filterCounts();
    expect(counts.all).toBe(3);
    expect(counts.completed).toBe(1);
    expect(counts.inProgress).toBe(1);
    expect(counts.translated).toBe(1);
  });

  test('sorts books correctly', () => {
    const catalog = booksCatalog(sampleBooks);
    catalog.init();

    // Default title sort: Agastyar Vaidyam, Bhagavad Gita, Tirukkural
    catalog.setSort('title');
    expect(catalog.sortedBooks()[0].title).toBe('Agastyar Vaidyam');
    expect(catalog.sortedBooks()[2].title).toBe('Tirukkural');

    // Sort by pages descending: Bhagavad Gita (700), Tirukkural (133), Agastyar Vaidyam (50)
    catalog.setSort('pages_desc');
    expect(catalog.sortedBooks()[0].title).toBe('Bhagavad Gita');
    expect(catalog.sortedBooks()[2].title).toBe('Agastyar Vaidyam');

    // Sort by progress descending: Tirukkural (100%), Agastyar Vaidyam (50%), Bhagavad Gita (0%)
    catalog.setSort('progress_desc');
    expect(catalog.sortedBooks()[0].title).toBe('Tirukkural');
    expect(catalog.sortedBooks()[2].title).toBe('Bhagavad Gita');
  });

  test('handles pagination properly', () => {
    // Create 25 books
    const manyBooks = Array.from({ length: 25 }, (_, i) => ({
      id: i + 1,
      title: `Book ${i + 1}`,
      author: 'Author',
      stats: { total_pages: 10, ocr_pages: 10, ocr_percentage: 100 },
    }));

    const catalog = booksCatalog(manyBooks);
    catalog.init();
    catalog.perPage = 9;

    expect(catalog.totalPages()).toBe(3);
    expect(catalog.paginatedBooks().length).toBe(9);
    expect(catalog.paginatedBooks()[0].title).toBe('Book 1');

    catalog.setPage(2);
    expect(catalog.page).toBe(2);
    expect(catalog.paginatedBooks().length).toBe(9);
    expect(catalog.paginatedBooks()[0].title).toBe('Book 10');

    catalog.setPage(3);
    expect(catalog.paginatedBooks().length).toBe(7); // 25 - 18 = 7

    // Clamps invalid page numbers
    catalog.setPage(99);
    expect(catalog.page).toBe(3);
  });

  test('clears search properly', () => {
    const catalog = booksCatalog(sampleBooks, 'gita');
    catalog.init();
    expect(catalog.searchQuery).toBe('gita');

    catalog.clearSearch();
    expect(catalog.query).toBe('');
    expect(catalog.searchQuery).toBe('');
    expect(catalog.page).toBe(1);
    expect(catalog.filteredBooks().length).toBe(3);
  });

  test('debounces search input', (done) => {
    const catalog = booksCatalog(sampleBooks);
    catalog.init();
    catalog.query = 'tiruk';
    catalog.onSearchInput();

    // Before 200ms debounce
    expect(catalog.searchQuery).toBe('');

    // After debounce timeout
    setTimeout(() => {
      expect(catalog.searchQuery).toBe('tiruk');
      done();
    }, 250);
  });
});
