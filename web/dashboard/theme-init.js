(function () {
  try {
    var saved = localStorage.getItem('d11-theme') || 'system';
    var isDark = saved === 'dark' || (saved === 'system' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme-mode', saved);
  } catch (e) {}
})();
