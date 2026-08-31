/**
 * Kalanjiyam Search Alpine.js component.
 * Provides debounce handling, auto-suggest querying, selection, and keyboard navigation.
 */
export default (initialQuery = "", suggestUrl = "/search/suggest") => ({
  query: initialQuery,
  suggestions: [],
  isOpen: false,
  isLoading: false,
  selectedIndex: -1,

  init() {
    this.query = initialQuery;
  },

  async fetchSuggestions() {
    const q = (this.query || "").trim();
    if (q.length < 2) {
      this.suggestions = [];
      this.isOpen = false;
      this.selectedIndex = -1;
      return;
    }

    this.isLoading = true;
    try {
      const url = suggestUrl + "?q=" + encodeURIComponent(q);
      const resp = await fetch(url);
      if (!resp.ok) {
        this.suggestions = [];
        this.isOpen = false;
        return;
      }
      const data = await resp.json();
      this.suggestions = Array.isArray(data) ? data : [];
      this.isOpen = this.suggestions.length > 0;
      this.selectedIndex = -1;
    } catch {
      this.suggestions = [];
      this.isOpen = false;
    } finally {
      this.isLoading = false;
    }
  },

  selectSuggestion(item) {
    if (item && item.title) {
      this.query = item.title;
      this.isOpen = false;
      this.selectedIndex = -1;
      if (this.$refs && this.$refs.form) {
        this.$refs.form.submit();
      }
    }
  },

  selectByIndex(index) {
    if (index >= 0 && index < this.suggestions.length) {
      this.selectSuggestion(this.suggestions[index]);
    }
  },

  navigateDown() {
    if (!this.isOpen || this.suggestions.length === 0) return;
    if (this.selectedIndex < this.suggestions.length - 1) {
      this.selectedIndex += 1;
    } else {
      this.selectedIndex = 0;
    }
  },

  navigateUp() {
    if (!this.isOpen || this.suggestions.length === 0) return;
    if (this.selectedIndex > 0) {
      this.selectedIndex -= 1;
    } else {
      this.selectedIndex = this.suggestions.length - 1;
    }
  },

  handleEnter(e) {
    if (this.isOpen && this.selectedIndex >= 0 && this.selectedIndex < this.suggestions.length) {
      if (e && e.preventDefault) e.preventDefault();
      this.selectByIndex(this.selectedIndex);
    }
  },

  clear() {
    this.query = "";
    this.suggestions = [];
    this.isOpen = false;
    this.selectedIndex = -1;
    if (this.$refs && this.$refs.input) {
      this.$refs.input.focus();
    }
  },

  close() {
    this.isOpen = false;
    this.selectedIndex = -1;
  },
});
