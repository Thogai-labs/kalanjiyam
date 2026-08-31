/**
 * Space-Saving Top-K streaming algorithm for search queries.
 * Based on Metwally et al. (2005): "Efficient Computation of Frequent and Top-k Elements in Data Streams".
 *
 * Persists frequent search terms in localStorage with bounded memory (capacity M).
 * Automatically falls back to curated heritage topics when fewer than K user queries exist.
 */

export const STORAGE_KEY = 'kalanjiyam_topk_searches_v1';
export const DEFAULT_CAPACITY = 20;
export const DEFAULT_K = 6;
export const DEFAULT_TOPICS = [
  'Siddha',
  'Agastyar',
  'நோய்',
  'மருத்துவம்',
  'Medicine',
  'Manuscript',
];

export class TopKSearchTracker {
  constructor(storageKey = STORAGE_KEY, capacity = DEFAULT_CAPACITY) {
    this.storageKey = storageKey;
    this.capacity = capacity;
  }

  /**
   * Safe retrieval of items from localStorage.
   * Each item has: { key: string, term: string, count: number, error: number, lastUsed: number }
   */
  getItems() {
    try {
      if (typeof window === 'undefined' || !window.localStorage) return [];
      const raw = window.localStorage.getItem(this.storageKey);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  /**
   * Safe storage persistence to localStorage.
   */
  saveItems(items) {
    try {
      if (typeof window === 'undefined' || !window.localStorage) return;
      window.localStorage.setItem(this.storageKey, JSON.stringify(items));
    } catch (e) {
      console.warn('TopKSearchTracker: Could not persist to localStorage', e);
    }
  }

  /**
   * Records a search query using the Space-Saving stream algorithm.
   * @param {string} query - The search query term
   */
  record(query) {
    if (!query || typeof query !== 'string') return;
    const trimmed = query.trim();
    if (trimmed.length < 2) return;

    const normalizedKey = trimmed.toLowerCase();
    const items = this.getItems();
    const now = Date.now();

    const existingIndex = items.findIndex((item) => item.key === normalizedKey);

    if (existingIndex !== -1) {
      // Hit: Item already monitored in stream summary
      items[existingIndex].count += 1;
      items[existingIndex].lastUsed = now;
      items[existingIndex].term = trimmed; // Update display term capitalization
    } else if (items.length < this.capacity) {
      // Miss with available capacity: Insert new item with count=1 and error=0
      items.push({
        key: normalizedKey,
        term: trimmed,
        count: 1,
        error: 0,
        lastUsed: now,
      });
    } else {
      // Miss at capacity: Space-Saving replacement
      // Find item with minimum count (tie-break with oldest lastUsed)
      let minIdx = 0;
      for (let i = 1; i < items.length; i += 1) {
        if (
          items[i].count < items[minIdx].count ||
          (items[i].count === items[minIdx].count &&
            (items[i].lastUsed || 0) < (items[minIdx].lastUsed || 0))
        ) {
          minIdx = i;
        }
      }

      const minCount = items[minIdx].count;

      // In Space-Saving, replacing element gets minCount + 1, error = minCount
      items[minIdx] = {
        key: normalizedKey,
        term: trimmed,
        count: minCount + 1,
        error: minCount,
        lastUsed: now,
      };
    }

    this.saveItems(items);
  }

  /**
   * Retrieves the Top-K queries sorted by frequency (descending) and recency.
   * If fewer than K queries are stored, fills remainder with default heritage topics.
   *
   * @param {number} k - Number of top items to return
   * @param {string[]} defaults - Default topics to fill remainder
   * @returns {Array<{ term: string, count: number, isPersonal: boolean }>}
   */
  getTopK(k = DEFAULT_K, defaults = DEFAULT_TOPICS) {
    const items = this.getItems();

    // Sort by count descending, break ties by recency
    items.sort((a, b) => {
      if (b.count !== a.count) {
        return b.count - a.count;
      }
      return (b.lastUsed || 0) - (a.lastUsed || 0);
    });

    const result = [];
    const seenKeys = new Set();

    for (let i = 0; i < items.length && result.length < k; i += 1) {
      const item = items[i];
      if (!seenKeys.has(item.key)) {
        seenKeys.add(item.key);
        result.push({
          term: item.term,
          count: item.count,
          isPersonal: true,
        });
      }
    }

    // Fill up to k using default heritage topics
    if (defaults && Array.isArray(defaults)) {
      for (let i = 0; i < defaults.length && result.length < k; i += 1) {
        const def = defaults[i];
        const defKey = def.trim().toLowerCase();
        if (!seenKeys.has(defKey)) {
          seenKeys.add(defKey);
          result.push({
            term: def,
            count: 0,
            isPersonal: false,
          });
        }
      }
    }

    return result;
  }

  /**
   * Removes a single query term from history.
   */
  remove(query) {
    if (!query) return;
    const normalizedKey = query.trim().toLowerCase();
    const items = this.getItems().filter((item) => item.key !== normalizedKey);
    this.saveItems(items);
  }

  /**
   * Clears all recorded search history.
   */
  clear() {
    try {
      if (typeof window === 'undefined' || !window.localStorage) return;
      window.localStorage.removeItem(this.storageKey);
    } catch {}
  }
}

export const topKTracker = new TopKSearchTracker();
export default topKTracker;
