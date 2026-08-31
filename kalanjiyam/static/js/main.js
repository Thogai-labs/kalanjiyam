/* globals Alpine, Sanscript */

import { $ } from './core.ts';
import Dictionary from './dictionary';
import HamburgerButton from './hamburger-button';
import HTMLPoller from './html-poller';
import Reader from './reader';
import Proofer from './proofer';
import SortableList from './sortable-list';
import SearchBar from './search-bar';
import topKTracker, { DEFAULT_TOPICS } from './topk-search';

window.addEventListener('alpine:init', () => {
  Alpine.data('dictionary', Dictionary);
  Alpine.data('htmlPoller', HTMLPoller);
  Alpine.data('reader', Reader);
  Alpine.data('proofer', Proofer);
  Alpine.data('sortableList', SortableList);
  Alpine.data('searchBar', SearchBar);
  Alpine.data('topKTopics', (k = 6, defaultTopics = DEFAULT_TOPICS, searchUrl = '/search/') => ({
    topics: [],
    init() {
      this.refresh();
      // Also listen for any storage update events across tabs
      window.addEventListener('storage', (e) => {
        if (e.key === topKTracker.storageKey) {
          this.refresh();
        }
      });
    },
    refresh() {
      this.topics = topKTracker.getTopK(k, defaultTopics);
    },
    selectTopic(term) {
      if (term) {
        topKTracker.record(term);
        window.location.href = `${searchUrl}?q=${encodeURIComponent(term)}`;
      }
    },
    clearHistory() {
      topKTracker.clear();
      this.refresh();
    },
    hasPersonalSearches() {
      return this.topics.some((t) => t.isPersonal);
    },
  }));
});

(() => {
  HamburgerButton.init();
})();
