import { TopKSearchTracker, STORAGE_KEY } from "@/topk-search";

describe("TopKSearchTracker (Space-Saving algorithm in localStorage)", () => {
  let mockStore = {};

  beforeEach(() => {
    mockStore = {};
    const localStorageMock = {
      getItem: jest.fn((key) => mockStore[key] || null),
      setItem: jest.fn((key, value) => {
        mockStore[key] = value.toString();
      }),
      removeItem: jest.fn((key) => {
        delete mockStore[key];
      }),
      clear: jest.fn(() => {
        mockStore = {};
      }),
    };
    Object.defineProperty(window, "localStorage", {
      value: localStorageMock,
      writable: true,
    });
  });

  test("initializes with empty store", () => {
    const tracker = new TopKSearchTracker("test_key", 5);
    expect(tracker.getItems()).toEqual([]);
  });

  test("ignores invalid or too short queries", () => {
    const tracker = new TopKSearchTracker("test_key", 5);
    tracker.record("");
    tracker.record("   ");
    tracker.record("a");
    tracker.record(null);
    expect(tracker.getItems().length).toBe(0);
  });

  test("records distinct queries and increments counts on repeated queries", () => {
    const tracker = new TopKSearchTracker("test_key", 5);
    tracker.record("Siddha");
    tracker.record("Agastyar");
    tracker.record("siddha"); // case-insensitive match

    const items = tracker.getItems();
    expect(items.length).toBe(2);

    const siddha = items.find((i) => i.key === "siddha");
    expect(siddha).toBeDefined();
    expect(siddha.count).toBe(2);
    expect(siddha.term).toBe("siddha");
  });

  test("applies Space-Saving eviction when capacity is reached", () => {
    // Capacity of 3
    const tracker = new TopKSearchTracker("test_key", 3);
    tracker.record("Apple");
    tracker.record("Apple");
    tracker.record("Banana");
    tracker.record("Cherry");

    // All 3 slots filled:
    // Apple (count=2), Banana (count=1), Cherry (count=1)
    expect(tracker.getItems().length).toBe(3);

    // Now insert a 4th query "Date"
    // The algorithm finds the minimum count item (Banana or Cherry, with min count=1)
    // and replaces it with Date, setting count = minCount + 1 (1 + 1 = 2)
    tracker.record("Date");
    const items = tracker.getItems();
    expect(items.length).toBe(3);

    const dateItem = items.find((i) => i.key === "date");
    expect(dateItem).toBeDefined();
    expect(dateItem.count).toBe(2);
    expect(dateItem.error).toBe(1);
  });

  test("getTopK sorts by count descending and recency, filling with defaults", () => {
    const tracker = new TopKSearchTracker("test_key", 10);
    tracker.record("Medicine");
    tracker.record("Medicine");
    tracker.record("Tamil Veda");

    const defaults = ["Siddha", "Agastyar", "Medicine", "Manuscript"];
    const topK = tracker.getTopK(4, defaults);

    expect(topK.length).toBe(4);
    // Highest count
    expect(topK[0].term).toBe("Medicine");
    expect(topK[0].count).toBe(2);
    expect(topK[0].isPersonal).toBe(true);

    // Next highest
    expect(topK[1].term).toBe("Tamil Veda");
    expect(topK[1].isPersonal).toBe(true);

    // Remaining slots filled from defaults without duplicates
    expect(topK[2].term).toBe("Siddha");
    expect(topK[2].isPersonal).toBe(false);

    expect(topK[3].term).toBe("Agastyar");
    expect(topK[3].isPersonal).toBe(false);
  });

  test("remove and clear methods update localStorage correctly", () => {
    const tracker = new TopKSearchTracker("test_key", 5);
    tracker.record("Topic A");
    tracker.record("Topic B");
    expect(tracker.getItems().length).toBe(2);

    tracker.remove("Topic A");
    expect(tracker.getItems().length).toBe(1);
    expect(tracker.getItems()[0].key).toBe("topic b");

    tracker.clear();
    expect(tracker.getItems().length).toBe(0);
  });
});
