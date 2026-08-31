/**
 * Alpine.js component for the Digital Books Catalog (/books/).
 * Designed following Google design philosophy:
 * - High legibility and accessibility for middle-aged readers
 * - Instant debounced filtering by title, author, and description
 * - Category filter chips (All, Completed OCR, In Progress, Translated)
 * - Sorting by Title, Pages, Progress
 * - Grid and compact List view modes
 * - Client-side pagination to eliminate excessive scrolling
 */
export default function booksCatalog(dataSource = [], initialQuery = '', initialPage = 1, initialPerPage = 9) {
  let books = [];
  if (Array.isArray(dataSource)) {
    books = dataSource;
  } else if (typeof dataSource === 'string' && dataSource.length > 0) {
    const el = document.getElementById(dataSource);
    if (el && el.textContent) {
      try {
        const parsed = JSON.parse(el.textContent);
        if (Array.isArray(parsed)) {
          books = parsed;
        }
      } catch (e) {
        console.error('Failed to parse books JSON data element:', e);
      }
    }
  } else if (typeof window !== 'undefined' && Array.isArray(window.__KALANJIYAM_BOOKS__)) {
    books = window.__KALANJIYAM_BOOKS__;
  }

  // Parse query parameters from window.location if available and not explicitly provided
  let urlQuery = initialQuery || '';
  let urlPage = Number(initialPage) || 1;
  let urlPerPage = Number(initialPerPage) || 9;

  if (typeof window !== 'undefined' && window.location && window.location.search) {
    try {
      const params = new URLSearchParams(window.location.search);
      if (!urlQuery && params.has('q')) {
        urlQuery = params.get('q') || '';
      }
      if (urlPage === 1 && params.has('page')) {
        const p = parseInt(params.get('page'), 10);
        if (!Number.isNaN(p) && p >= 1) {
          urlPage = p;
        }
      }
      if (urlPerPage === 9 && params.has('per_page')) {
        const pp = parseInt(params.get('per_page'), 10);
        if ([9, 12, 18, 27, 36].includes(pp)) {
          urlPerPage = pp;
        }
      }
    } catch (e) {
      // Ignore URL parsing errors
    }
  }

  return {
    allBooks: books,
    query: urlQuery,
    searchQuery: urlQuery,
    activeFilter: 'all', // 'all' | 'completed' | 'in_progress' | 'translated'
    sortBy: 'title', // 'title' | 'pages_desc' | 'progress_desc'
    viewMode: 'grid', // 'grid' | 'list'
    page: urlPage,
    perPage: urlPerPage,
    debounceTimer: null,

    init() {
      // Re-check dataSource if books was empty initially (in case DOM element was rendered after component factory)
      if (this.allBooks.length === 0) {
        if (typeof dataSource === 'string' && dataSource.length > 0) {
          const el = document.getElementById(dataSource);
          if (el && el.textContent) {
            try {
              const parsed = JSON.parse(el.textContent);
              if (Array.isArray(parsed)) {
                this.allBooks = parsed;
              }
            } catch (e) {
              // Ignore parse error
            }
          }
        } else if (typeof window !== 'undefined' && Array.isArray(window.__KALANJIYAM_BOOKS__)) {
          this.allBooks = window.__KALANJIYAM_BOOKS__;
        }
      }

      // Sync with URL parameters on back/forward browser navigation
      if (typeof window !== 'undefined' && window.addEventListener) {
        window.addEventListener('popstate', () => {
          try {
            const params = new URLSearchParams(window.location.search);
            const p = parseInt(params.get('page') || '1', 10);
            this.page = Number.isNaN(p) ? 1 : Math.max(1, p);
            if (params.has('q')) {
              this.query = params.get('q') || '';
              this.searchQuery = this.query.trim();
            }
          } catch (e) {
            // Ignore
          }
        });
      }
    },

    // Synchronize current page and query parameters to the browser URL
    updateUrl() {
      if (typeof window === 'undefined' || !window.history || !window.location) {
        return;
      }
      try {
        const url = new URL(window.location.href);
        if (this.page > 1) {
          url.searchParams.set('page', String(this.page));
        } else {
          url.searchParams.delete('page');
        }

        if (this.searchQuery) {
          url.searchParams.set('q', this.searchQuery);
        } else {
          url.searchParams.delete('q');
        }

        if (this.perPage !== 9) {
          url.searchParams.set('per_page', String(this.perPage));
        } else {
          url.searchParams.delete('per_page');
        }

        window.history.pushState(null, '', url.toString());
      } catch (e) {
        // Ignore errors in environments where URL manipulation is restricted
      }
    },

    // Debounced search input handler
    onSearchInput() {
      if (this.debounceTimer) {
        clearTimeout(this.debounceTimer);
      }
      this.debounceTimer = setTimeout(() => {
        this.searchQuery = (this.query || '').trim();
        this.page = 1;
        this.updateUrl();
      }, 200);
    },

    clearSearch() {
      this.query = '';
      this.searchQuery = '';
      this.page = 1;
      this.updateUrl();
      if (this.$refs && this.$refs.searchInput) {
        this.$refs.searchInput.focus();
      }
    },

    setFilter(filter) {
      this.activeFilter = filter;
      this.page = 1;
    },

    setSort(sort) {
      this.sortBy = sort;
      this.page = 1;
    },

    setViewMode(mode) {
      this.viewMode = mode;
      // In list view, default to 12 items per page if standard
      if (mode === 'list' && this.perPage === 9) {
        this.perPage = 12;
      } else if (mode === 'grid' && this.perPage === 12) {
        this.perPage = 9;
      }
      this.page = 1;
      this.updateUrl();
    },

    setPerPage(count) {
      const num = parseInt(count, 10);
      if ([9, 12, 18, 27, 36].includes(num)) {
        this.perPage = num;
        this.page = 1;
        this.updateUrl();
      }
    },

    // Filtered books based on search query and active filter chip
    filteredBooks() {
      const q = (this.searchQuery || '').toLowerCase();
      return this.allBooks.filter((book) => {
        // Query match
        if (q) {
          const title = (book.title || '').toLowerCase();
          const author = (book.author || '').toLowerCase();
          const desc = (book.description || '').toLowerCase();
          if (!title.includes(q) && !author.includes(q) && !desc.includes(q)) {
            return false;
          }
        }

        // Category filter match
        const ocrPercent = book.stats ? (book.stats.ocr_percentage || 0) : 0;
        const transPages = book.stats ? (book.stats.translated_pages || 0) : 0;

        if (this.activeFilter === 'completed') {
          return ocrPercent >= 100;
        }
        if (this.activeFilter === 'in_progress') {
          return ocrPercent > 0 && ocrPercent < 100;
        }
        if (this.activeFilter === 'translated') {
          return transPages > 0;
        }

        return true;
      });
    },

    // Sorted books
    sortedBooks() {
      const list = [...this.filteredBooks()];
      if (this.sortBy === 'title') {
        list.sort((a, b) => (a.title || '').localeCompare(b.title || '', undefined, { numeric: true, sensitivity: 'base' }));
      } else if (this.sortBy === 'pages_desc') {
        list.sort((a, b) => {
          const pA = a.stats ? (a.stats.total_pages || 0) : 0;
          const pB = b.stats ? (b.stats.total_pages || 0) : 0;
          return pB - pA;
        });
      } else if (this.sortBy === 'progress_desc') {
        list.sort((a, b) => {
          const oA = a.stats ? (a.stats.ocr_percentage || 0) : 0;
          const oB = b.stats ? (b.stats.ocr_percentage || 0) : 0;
          return oB - oA;
        });
      }
      return list;
    },

    // Total page count
    totalPages() {
      const total = this.sortedBooks().length;
      return Math.max(1, Math.ceil(total / this.perPage));
    },

    // Sliced items for the active page
    paginatedBooks() {
      const sorted = this.sortedBooks();
      const maxPage = this.totalPages();
      if (this.page > maxPage) {
        this.page = maxPage;
      }
      const start = (this.page - 1) * this.perPage;
      return sorted.slice(start, start + this.perPage);
    },

    setPage(p) {
      const max = this.totalPages();
      if (p >= 1 && p <= max) {
        this.page = p;
        this.updateUrl();
        const el = document.getElementById('catalog-content');
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }
    },

    // Counts for filter chips
    filterCounts() {
      let completed = 0;
      let inProgress = 0;
      let translated = 0;

      for (const b of this.allBooks) {
        const ocr = b.stats ? (b.stats.ocr_percentage || 0) : 0;
        const trans = b.stats ? (b.stats.translated_pages || 0) : 0;
        if (ocr >= 100) completed += 1;
        else if (ocr > 0) inProgress += 1;
        if (trans > 0) translated += 1;
      }

      return {
        all: this.allBooks.length,
        completed,
        inProgress,
        translated,
      };
    },

    // Windowed page numbers
    pageNumbers() {
      const total = this.totalPages();
      const current = this.page;
      const pages = [];

      let start = Math.max(1, current - 2);
      let end = Math.min(total, start + 4);
      if (end - start < 4) {
        start = Math.max(1, end - 4);
      }

      for (let i = start; i <= end; i += 1) {
        pages.push(i);
      }
      return pages;
    },

    // Full Google-style page items including first, last, and ellipsis ('…')
    pageItems() {
      const total = this.totalPages();
      const current = this.page;
      if (total <= 7) {
        return Array.from({ length: total }, (_, i) => i + 1);
      }

      const items = [];
      let start = Math.max(1, current - 2);
      let end = Math.min(total, current + 2);

      if (current <= 4) {
        start = 1;
        end = 5;
      } else if (current >= total - 3) {
        start = total - 4;
        end = total;
      }

      if (start > 1) {
        items.push(1);
        if (start > 2) {
          items.push('…');
        }
      }

      for (let i = start; i <= end; i += 1) {
        items.push(i);
      }

      if (end < total) {
        if (end < total - 1) {
          items.push('…');
        }
        items.push(total);
      }

      return items;
    },
  };
}
