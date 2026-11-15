(() => {
  const storageKey = 'workbench-reset-read-v1';
  let read = loadRead();
  let scheduled = false;

  function loadRead() {
    try {
      const values = JSON.parse(localStorage.getItem(storageKey) || '[]');
      return new Set(Array.isArray(values) ? values.filter(value => typeof value === 'string') : []);
    } catch {
      return new Set();
    }
  }

  function revision(row) {
    // The fallback also handles a previously cached backend snapshot.
    return row.dataset.resetRevision || JSON.stringify([
      row.getAttribute('href'), row.querySelector('[data-reset-title]')?.textContent,
      row.querySelector('[data-reset-time]')?.textContent
    ]);
  }

  function reconcile() {
    scheduled = false;
    document.querySelectorAll('.live-resets .reset-events').forEach(list => {
      const rows = Array.from(list.querySelectorAll('.reset-event'));
      const ordered = [...rows].sort((a, b) => Number(b.dataset.resetTimestamp || 0) - Number(a.dataset.resetTimestamp || 0));
      if (rows.some((row, index) => row !== ordered[index])) list.append(...ordered);
      for (const row of rows) {
        const unread = !read.has(revision(row));
        let dot = row.querySelector('.reset-unread-dot');
        if (unread && !dot) {
          dot = document.createElement('span');
          dot.className = 'reset-unread-dot';
          dot.setAttribute('aria-hidden', 'true');
          row.append(dot);
        }
        if (!unread && dot) dot.remove();
        row.classList.toggle('is-unread', unread);
        const title = row.querySelector('[data-reset-title]')?.textContent || '';
        const time = row.querySelector('[data-reset-time]')?.textContent || '';
        row.setAttribute('aria-label', `${unread ? '未查看，' : ''}${title}，${time}`);
        row.title = unread ? '新消息，点击查看' : '点击查看';
      }
    });
  }

  function markRead(event) {
    if (event.type === 'auxclick' && event.button !== 1) return;
    const link = event.target.closest?.('.live-resets a');
    if (!link) return;
    const widget = link.closest('.live-resets');
    const rows = link.matches('.reset-event') ? [link] :
      link.matches('[data-reset-open-all], .widget-header a') ? widget.querySelectorAll('.reset-event') : [];
    if (!rows.length) return;
    for (const value of loadRead()) read.add(value);
    for (const row of rows) read.add(revision(row));
    try {
      localStorage.setItem(storageKey, JSON.stringify([...read]));
    } catch {
      // Keep click feedback working for this page when storage is unavailable.
    }
    reconcile();
  }

  document.addEventListener('click', markRead);
  document.addEventListener('auxclick', markRead);
  window.addEventListener('storage', event => {
    if (event.key !== storageKey && event.key !== null) return;
    read = loadRead();
    reconcile();
  });
  const observer = new MutationObserver(records => {
    const relevant = records.some(record => record.target.closest?.('.live-resets') ||
      [...record.addedNodes].some(node => node.nodeType === 1 &&
        (node.matches('.live-resets') || node.querySelector('.live-resets'))));
    if (!relevant || scheduled) return;
    scheduled = true;
    queueMicrotask(reconcile);
  });
  observer.observe(document.body, {childList: true, subtree: true});
  reconcile();
})();
