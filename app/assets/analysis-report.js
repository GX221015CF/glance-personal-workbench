(() => {
  'use strict';
  const format = value => new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 4}).format(value);
  document.querySelectorAll('.simulator').forEach(panel => {
    const input = panel.querySelector('input[type="range"]');
    const output = panel.querySelector('output');
    const current = panel.querySelector('.simulator-current');
    const bar = panel.querySelector('progress');
    if (!input || !output || !current || !bar) return;
    const factor = Number(panel.dataset.factor), offset = Number(panel.dataset.offset);
    const min = Number(input.min), max = Number(input.max);
    const update = () => {
      const value = Number(input.value);
      const result = factor * value + offset;
      current.textContent = format(value);
      output.textContent = format(result) + (panel.dataset.unit || '');
      // The bar represents the selected input's position, including negative outputs.
      bar.value = 100 * (value - min) / (max - min);
    };
    input.addEventListener('input', update);
    update();
  });
})();
