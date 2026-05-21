/* globals Alpine, Sanscript */

import { $ } from './core.ts';
import Dictionary from './dictionary';
import HamburgerButton from './hamburger-button';
import HTMLPoller from './html-poller';
import Reader from './reader';
import Proofer from './proofer';
import SortableList from './sortable-list';

window.addEventListener('alpine:init', () => {
  Alpine.data('dictionary', Dictionary);
  Alpine.data('htmlPoller', HTMLPoller);
  Alpine.data('reader', Reader);
  Alpine.data('proofer', Proofer);
  Alpine.data('sortableList', SortableList);
});

(() => {
  HamburgerButton.init();

  const body = document.body;
  const storedTheme = window.localStorage.getItem('kalanjiyam-theme');
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
  body.dataset.theme = storedTheme || (prefersDark ? 'dark' : 'light');

  const themeToggle = document.querySelector('[data-theme-toggle]');
  if (themeToggle) {
    themeToggle.addEventListener('click', () => {
      const theme = body.dataset.theme === 'dark' ? 'light' : 'dark';
      body.dataset.theme = theme;
      window.localStorage.setItem('kalanjiyam-theme', theme);
    });
  }

  const hamburger = document.getElementById('hamburger');
  const navbar = document.getElementById('navbar');
  if (hamburger && navbar) {
    hamburger.addEventListener('click', () => {
      const isOpen = hamburger.getAttribute('aria-expanded') === 'true';
      hamburger.setAttribute('aria-expanded', String(!isOpen));
      navbar.setAttribute('aria-hidden', String(isOpen));
      navbar.classList.toggle('hidden');
    });
  }

  const header = document.querySelector('.header-nav');
  window.addEventListener('scroll', () => {
    if (!header) return;
    header.classList.toggle('shadow-2xl', window.scrollY > 24);
  });
})();
