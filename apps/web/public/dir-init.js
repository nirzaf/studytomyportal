// Set the English interface direction before the first paint. Browser locale
// preferences and saved language cookies do not affect the application UI.
(function () {
  var el = document.documentElement;
  el.setAttribute('lang', 'en');
  el.setAttribute('dir', 'ltr');
  el.style.setProperty('--dir', '1');
})();
