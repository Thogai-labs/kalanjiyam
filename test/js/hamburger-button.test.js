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

  test('removes x-cloak attribute when toggled without Alpine', () => {
    const $drawer = document.getElementById('mobile-drawer');
    const $backdrop = document.getElementById('mobile-backdrop');
    $drawer.setAttribute('x-cloak', '');
    $backdrop.setAttribute('x-cloak', '');

    HamburgerButton.init();
    const $ham = document.getElementById('hamburger');
    $ham.click();

    expect($drawer.hasAttribute('x-cloak')).toBe(false);
    expect($backdrop.hasAttribute('x-cloak')).toBe(false);
  });

  test('closes drawer when close button or backdrop is clicked without Alpine', () => {
    document.body.innerHTML = `
      <button id="hamburger" type="button">Toggle</button>
      <div id="mobile-drawer" class="hidden">
        <button id="mobile-drawer-close" type="button">Close</button>
      </div>
      <div id="mobile-backdrop" class="hidden"></div>
      <ul id="navbar"></ul>
    `;
    HamburgerButton.init();
    const $ham = document.getElementById('hamburger');
    const $drawer = document.getElementById('mobile-drawer');
    const $backdrop = document.getElementById('mobile-backdrop');
    const $close = document.getElementById('mobile-drawer-close');

    // Open drawer
    $ham.click();
    expect($drawer.classList.contains('hidden')).toBe(false);

    // Close via close button
    $close.click();
    expect($drawer.classList.contains('hidden')).toBe(true);
    expect($backdrop.classList.contains('hidden')).toBe(true);

    // Open again
    $ham.click();
    expect($drawer.classList.contains('hidden')).toBe(false);

    // Close via backdrop click
    $backdrop.click();
    expect($drawer.classList.contains('hidden')).toBe(true);
    expect($backdrop.classList.contains('hidden')).toBe(true);
  });
});
