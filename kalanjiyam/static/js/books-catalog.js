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
export default function booksCatalog(initialBooks = [], initialQuery = '') {
  return {
    allBooks: Array.isArray(initialBooks) ? initialBooks : [],
    query: initialQuery || '',
    searchQuery: initialQuery || '',
    activeFilter: 'all', // 'all' | 'completed' | 'in_progress' | 'translated'
    sortBy: 'title', // 'title' | 'pages_desc' | 'progress_desc'
    viewMode: 'grid', // 'grid' | 'list'
    page: 1,
    perPage: 9,
    debounceTimer: null,

    init() {
      this.query = initialQuery || '';
      this.searchQuery = (initialQuery || '').trim();
      this.page = 1;
    },

    // Debounced search input handler
    onSearchInput() {
      if (this.debounceTimer) {
        clearTimeout(this.debounceTimer);
      }
      this.debounceTimer = setTimeout(() => {
        this.searchQuery = (this.query || '').trim();
        this.page = 1;
      }, 200);
    },

    clearSearch() {
      this.query = '';
      this.searchQuery = '';
      this.page = 1;
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
      // In list view, allow 12 items per page since rows are compact
      this.perPage = mode === 'list' ? 12 : 9;
      this.page = 1;
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

    // Page window array for Google-style pagination
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
  };
}
