import { $ } from './core.ts';

export default (() => {
  function init() {
    const $ham = $('#hamburger');
    if ($ham) {
      // Alpine manages the mobile slide-in menu drawer reactively via @click on #hamburger.
      // If Alpine is not present, provide a graceful fallback to toggle the mobile drawer.
      $ham.addEventListener('click', (e) => {
        if (!window.Alpine) {
          e.preventDefault();
          const $drawer = $('#mobile-drawer');
          const $backdrop = $('#mobile-backdrop');
          if ($drawer) {
            $drawer.classList.toggle('hidden');
          }
          if ($backdrop) {
            $backdrop.classList.toggle('hidden');
          }
        }
      });
    }
  }

  return { init };
})();
