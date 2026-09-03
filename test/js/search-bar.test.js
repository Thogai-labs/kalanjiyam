import SearchBar from "@/search-bar";

describe("SearchBar Alpine.js component", () => {
  beforeEach(() => {
    window.fetch = jest.fn();
  });

  test("initializes with default query and state", () => {
    const s = SearchBar("siddha", "/search/suggest");
    s.init();
    expect(s.query).toBe("siddha");
    expect(s.suggestions).toEqual([]);
    expect(s.isOpen).toBe(false);
    expect(s.isLoading).toBe(false);
    expect(s.selectedIndex).toBe(-1);
  });

  test("fetchSuggestions skips fetch if query length < 2", async () => {
    const s = SearchBar("a");
    s.init();
    await s.fetchSuggestions();
    expect(window.fetch).not.toHaveBeenCalled();
    expect(s.suggestions).toEqual([]);
    expect(s.isOpen).toBe(false);
  });

  test("fetchSuggestions calls suggest endpoint and updates suggestions", async () => {
    window.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => [
        { title: "Agastyar Paripooranam", author: "Agastyar" },
        { title: "Agastyar Vaidya", author: "Agastyar" },
      ],
    });

    const s = SearchBar("agas");
    s.init();
    await s.fetchSuggestions();

    expect(window.fetch).toHaveBeenCalledWith("/search/suggest?q=agas");
    expect(s.suggestions.length).toBe(2);
    expect(s.suggestions[0].title).toBe("Agastyar Paripooranam");
    expect(s.isOpen).toBe(true);
  });

  test("handles fetch errors gracefully", async () => {
    window.fetch = jest.fn().mockRejectedValue(new Error("Network error"));

    const s = SearchBar("agas");
    s.init();
    await s.fetchSuggestions();

    expect(s.suggestions).toEqual([]);
    expect(s.isOpen).toBe(false);
    expect(s.isLoading).toBe(false);
  });

  test("selectSuggestion sets query and closes dropdown", () => {
    const s = SearchBar();
    s.init();
    const mockSubmit = jest.fn();
    s.$refs = { form: { submit: mockSubmit } };

    s.selectSuggestion({ title: "Siddha Medicine" });
    expect(s.query).toBe("Siddha Medicine");
    expect(s.isOpen).toBe(false);
    expect(mockSubmit).toHaveBeenCalled();
  });

  test("clear resets query, suggestions, and focuses input", () => {
    const s = SearchBar("test query");
    s.init();
    s.suggestions = [{ title: "Item" }];
    s.isOpen = true;
    const mockFocus = jest.fn();
    s.$refs = { input: { focus: mockFocus } };

    s.clear();
    expect(s.query).toBe("");
    expect(s.suggestions).toEqual([]);
    expect(s.isOpen).toBe(false);
    expect(mockFocus).toHaveBeenCalled();
  });

  test("keyboard navigation cycles through suggestions", () => {
    const s = SearchBar("agas");
    s.suggestions = [{ title: "A" }, { title: "B" }, { title: "C" }];
    s.isOpen = true;

    s.navigateDown();
    expect(s.selectedIndex).toBe(0);
    s.navigateDown();
    expect(s.selectedIndex).toBe(1);
    s.navigateDown();
    expect(s.selectedIndex).toBe(2);
    s.navigateDown();
    expect(s.selectedIndex).toBe(0); // wraps around

    s.navigateUp();
    expect(s.selectedIndex).toBe(2); // wraps around backward
  });

  test("handleEnter selects current keyboard suggestion", () => {
    const s = SearchBar("agas");
    s.suggestions = [{ title: "Selected Book" }];
    s.isOpen = true;
    s.selectedIndex = 0;
    const mockSubmit = jest.fn();
    s.$refs = { form: { submit: mockSubmit } };

    const mockEvent = { preventDefault: jest.fn() };
    s.handleEnter(mockEvent);

    expect(mockEvent.preventDefault).toHaveBeenCalled();
    expect(s.query).toBe("Selected Book");
    expect(mockSubmit).toHaveBeenCalled();
  });
});
