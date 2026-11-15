(() => {
  'use strict';
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: 'numeric', day: 'numeric',
    weekday: 'long', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  });
  function update() {
    if (document.hidden) return;
    const clock = document.getElementById('home-zh-clock');
    if (!clock) return;
    const parts = Object.fromEntries(formatter.formatToParts(new Date()).map(part => [part.type, part.value]));
    const values = {
      date: `${parts.month}月${parts.day}日`, year: `${parts.year}年`,
      time: `${parts.hour}:${parts.minute}`, weekday: parts.weekday
    };
    for (const [key, value] of Object.entries(values)) {
      const field = clock.querySelector(`[data-zh-${key}]`);
      if (field && field.textContent !== value) field.textContent = value;
    }
  }
  update();
  setInterval(update, 1000);
  document.addEventListener('visibilitychange', update);
})();
