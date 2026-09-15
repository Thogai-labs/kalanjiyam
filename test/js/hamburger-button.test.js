import HamburgerButton from '@/hamburger-button';

describe('HamburgerButton', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <button id="hamburger" type="button">Toggle</button>
      <div id="mobile-drawer" class="hidden"></div>
      <div id="mobile-backdrop" class="hidden"></div>
      <ul id="navbar"></ul>
    `;
    delete window.Alpine;
  });

  test('toggles mobile-drawer and mobile-backdrop classes when Alpine is not present', () => {
    HamburgerButton.init();
    const $ham = document.getElementById('hamburger');
    const $drawer = document.getElementById('mobile-drawer');
    const $backdrop = document.getElementById('mobile-backdrop');

    expect($drawer.classList.contains('hidden')).toBe(true);
    expect($backdrop.classList.contains('hidden')).toBe(true);

    $ham.click();

    expect($drawer.classList.contains('hidden')).toBe(false);
    expect($backdrop.classList.contains('hidden')).toBe(false);

    $ham.click();

    expect($drawer.classList.contains('hidden')).toBe(true);
    expect($backdrop.classList.contains('hidden')).toBe(true);
  });

  test('does not manually toggle classes when Alpine is present', () => {
    window.Alpine = {};
    HamburgerButton.init();
    const $ham = document.getElementById('hamburger');
    const $drawer = document.getElementById('mobile-drawer');
    const $backdrop = document.getElementById('mobile-backdrop');

    $ham.click();

    expect($drawer.classList.contains('hidden')).toBe(true);
    expect($backdrop.classList.contains('hidden')).toBe(true);
  });
});
