import { $ } from './core.ts';

export default (() => {
  function init() {
    const $ham = $('#hamburger');
    const $drawer = $('#mobile-drawer');
    const $backdrop = $('#mobile-backdrop');
    const $close = $('#mobile-drawer-close');

    if ($ham) {
      // Alpine manages the mobile slide-in menu drawer reactively via @click on #hamburger.
      // If Alpine is not present, provide a graceful fallback to toggle the mobile drawer.
      $ham.addEventListener('click', (e) => {
        if (!window.Alpine) {
          e.preventDefault();
          if ($drawer) {
            $drawer.removeAttribute('x-cloak');
            $drawer.classList.toggle('hidden');
          }
          if ($backdrop) {
            $backdrop.removeAttribute('x-cloak');
            $backdrop.classList.toggle('hidden');
          }
        }
      });
    }

    if ($close) {
      $close.addEventListener('click', (e) => {
        if (!window.Alpine) {
          e.preventDefault();
          if ($drawer) $drawer.classList.add('hidden');
          if ($backdrop) $backdrop.classList.add('hidden');
        }
      });
    }

    if ($backdrop) {
      $backdrop.addEventListener('click', (e) => {
        if (!window.Alpine) {
          e.preventDefault();
          if ($drawer) $drawer.classList.add('hidden');
          if ($backdrop) $backdrop.classList.add('hidden');
        }
      });
    }
  }

  return { init };
})();
