export const COMPONENT_CATEGORIES = [
  ["button", "按钮", "本批已完成 · 11 个效果"],
  ["toggle", "开关", "已完成 · 5 个效果"],
  ["tabs", "标签页", "已完成 · 5 个效果"],
  ["input", "输入框", "规划中"],
  ["select", "下拉菜单", "规划中"],
  ["list", "卡片与列表项", "规划中"],
  ["accordion", "折叠面板", "已完成 · 5 个效果"],
  ["dialog", "弹窗与抽屉", "规划中"],
  ["notice", "提示消息", "规划中"],
  ["progress", "加载与进度", "规划中"],
  ["navigation", "导航", "规划中"],
  ["drag", "拖动与排序", "规划中"],
];
export const BUTTON_SCENES = [
  ["general", "普通按钮"],
  ["refresh", "刷新按钮"],
  ["save", "保存按钮"],
  ["expand", "展开入口"],
];
export const BUTTON_GROUPS = [
  ["press", "按下反馈", "鼠标按住或键盘按下时发生，松开后恢复。"],
  ["click", "点击反馈", "完成一次点击后播放，鼠标和键盘使用同一个动作。"],
  [
    "task",
    "等待、成功与失败",
    "同一模拟任务用时 1200ms，任务完成后才显示结果。",
  ],
];
export const TRIGGERS = {
  press: "按下与松开",
  click: "点击后",
  loading: "等待过程",
  success: "完成成功",
  error: "失败与恢复",
};
export const TASK_MS = 1200;
const duration = (value, min = 80, max = 900) => ({
  duration: { label: "效果时长", unit: "ms", min, max, step: 10, value },
});
const parameter = (label, unit, min, max, step, value) => ({
  label,
  unit,
  min,
  max,
  step,
  value,
});
export const BUTTON_EFFECTS = [
  {
    id: "button-press-soft",
    name: "轻微缩小",
    group: "press",
    trigger: "press",
    kind: "soft",
    plain: "按住时轻轻缩小，松开后回到原来的大小。",
    parameters: {
      ...duration(150, 80, 400),
      scale: parameter("按下缩放", "", 0.9, 0.99, 0.01, 0.96),
    },
    use: "普通操作、工具栏按钮",
    caution: "缩放过大会让紧凑工具栏显得跳动。",
  },
  {
    id: "button-press-spring",
    name: "弹性回弹",
    group: "press",
    trigger: "press",
    kind: "spring",
    plain: "按下收缩，松开时越过一点原尺寸再稳定下来。",
    parameters: {
      ...duration(360, 160, 750),
      spring: parameter("回弹幅度", "%", 1, 10, 1, 4),
    },
    use: "轻量确认、可反复点击的入口",
    caution: "长任务按钮宜控制回弹幅度。",
  },
  {
    id: "button-press-solid",
    name: "实体下压",
    group: "press",
    trigger: "press",
    kind: "solid",
    plain: "按钮向下移动，底部阴影同时变薄。",
    parameters: {
      ...duration(130, 80, 400),
      depth: parameter("下压距离", "px", 2, 7, 1, 4),
    },
    use: "突出主操作、桌面工具入口",
    caution: "阴影占位会保留在演示区，避免推挤周围内容。",
  },
  {
    id: "button-click-ripple",
    name: "点击波纹",
    group: "click",
    trigger: "click",
    kind: "ripple",
    plain: "从你点下的位置扩散一圈波纹，键盘从中心扩散。",
    parameters: {
      ...duration(480, 160, 900),
      range: parameter("波纹直径", "%", 160, 320, 10, 240),
    },
    use: "普通按钮、列表内操作",
    caution: "波纹限制在按钮内部，连续点击会重新播放本轮。",
  },
  {
    id: "button-click-pulse",
    name: "边框脉冲",
    group: "click",
    trigger: "click",
    kind: "pulse",
    plain: "点击后，一圈边框向外扩散，再慢慢消失。",
    parameters: {
      ...duration(420, 160, 800),
      spread: parameter("扩散距离", "px", 3, 15, 1, 8),
    },
    use: "轻量确认、关注与收藏",
    caution: "按钮周围需要留一点空间。",
  },
  {
    id: "button-click-glow",
    name: "短暂光晕",
    group: "click",
    trigger: "click",
    kind: "glow",
    plain: "点击后亮起一层短暂光晕，随后恢复。",
    parameters: {
      ...duration(500, 180, 900),
      intensity: parameter("光晕强度", "%", 15, 65, 5, 35),
    },
    use: "深浅界面的主操作提示",
    caution: "高频操作宜降低强度，光晕不承担成功含义。",
  },
  {
    id: "button-click-fill",
    name: "方向填充",
    group: "click",
    trigger: "click",
    kind: "fill",
    plain: "点击后，一层浅色从左向右扫过按钮。",
    parameters: {
      ...duration(380, 140, 800),
      direction: parameter("填充方向", "", 0, 1, 1, 0),
    },
    use: "继续、前往、提交前的点击确认",
    caution: "这里的填充仅表示点击已响应。",
  },
  {
    id: "button-click-icon",
    name: "图标轻推",
    group: "click",
    trigger: "click",
    kind: "icon",
    plain: "点击后，右侧箭头轻移一下再回来，文字留在原位。",
    parameters: {
      ...duration(300, 120, 700),
      distance: parameter("图标位移", "px", 2, 12, 1, 6),
    },
    use: "打开详情、下一步、展开入口",
    caution: "图标的移动方向应与入口含义协调。",
  },
  {
    id: "button-wait-spinner",
    name: "等待状态过渡",
    group: "task",
    trigger: "loading",
    kind: "spinner",
    plain: "开始模拟任务后进入等待，任务完成后恢复可操作状态。",
    parameters: {
      ...duration(180, 80, 500),
      spin: parameter("转一圈用时", "ms", 400, 1400, 50, 800),
    },
    use: "刷新、查询、保存的等待阶段",
    caution: "转圈仅在任务进行时播放，模拟任务可重置。",
  },
  {
    id: "button-success-check",
    name: "成功勾选与文字",
    group: "task",
    trigger: "success",
    kind: "check",
    plain: "同一模拟任务完成后，出现勾选和成功文字。",
    parameters: {
      ...duration(320, 120, 750),
      hold: parameter("结果停留", "ms", 700, 2600, 100, 1400),
    },
    use: "保存、提交、同步完成",
    caution: "结果来自模拟任务完成，点击本身不会直接触发成功。",
  },
  {
    id: "button-error-recover",
    name: "失败提示与恢复",
    group: "task",
    trigger: "error",
    kind: "error",
    plain: "模拟任务结束后显示失败，保留重试入口，再次点击可恢复。",
    parameters: {
      ...duration(220, 100, 650),
      distance: parameter("提示位移", "px", 0, 6, 1, 3),
    },
    use: "保存失败、刷新来源暂不可用",
    caution: "失败有文字说明，下一次重试成功用于演示恢复。",
  },
].map((effect, index) => ({
  ...effect,
  version: 1,
  number: String(index + 1).padStart(2, "0"),
  implementation:
    "本项目自主实现，使用原生 CSS 与 JavaScript，未复制外部组件源码。",
}));

export const effectById = (id) =>
  BUTTON_EFFECTS.find((effect) => effect.id === id);
export function recommendedParams(effect) {
  return Object.fromEntries(
    Object.entries(effect.parameters).map(([key, spec]) => [key, spec.value]),
  );
}
export function normalizeEffectParams(effect, input = {}) {
  return Object.fromEntries(
    Object.entries(effect.parameters).map(([key, spec]) => {
      const number = Number(input?.[key]);
      return [
        key,
        Number.isFinite(number)
          ? Number(
              (
                Math.round(
                  Math.min(spec.max, Math.max(spec.min, number)) / spec.step,
                ) * spec.step
              ).toFixed(4),
            )
          : spec.value,
      ];
    }),
  );
}
export function formatParameters(effect, params, speed = 1) {
  return Object.entries(effect.parameters)
    .map(([key, spec]) => {
      let value = params[key];
      if (key === "direction")
        return "填充方向 " + (value === 0 ? "左 → 右" : "右 → 左");
      if (speed !== 1 && ["duration", "hold", "spin"].includes(key))
        value = Math.round(value / speed);
      return `${spec.label} ${value}${spec.unit}`;
    })
    .join(" · ");
}
export const BUTTON_SOURCES = [
  {
    name: "MDN Element.animate()",
    url: "https://developer.mozilla.org/en-US/docs/Web/API/Element/animate",
    evidence: "本轮已读取原生动画接口说明，用于按需播放与取消的实现依据。",
  },
  {
    name: "W3C Button Pattern",
    url: "https://www.w3.org/WAI/ARIA/apg/patterns/button/",
    evidence: "本轮已核查原生按钮的 Enter、Space、焦点与不可用状态规则。",
  },
  {
    name: "MDN 减少动效",
    url: "https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion",
    evidence: "本轮已读取媒体查询说明，静态降级仍保留任务文字与结果。",
  },
];
