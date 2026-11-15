import {
  TASK_MS,
  effectById,
  normalizeEffectParams,
  recommendedParams,
} from "./button-catalog.mjs";

export const FX_ICON =
  '<svg class="fx-icon" aria-hidden="true" viewBox="0 0 24 24"><path class="fx-arrow" d="M5 12h14m-5-5 5 5-5 5"/><path class="fx-check" d="m5 12 4 4 10-10"/><path class="fx-error-mark" d="m7 7 10 10M17 7 7 17"/></svg>';
export function effectButtonMarkup(text = "试一下", extra = "") {
  return `<button type="button" class="fx-button" ${extra}><span class="fx-clip" aria-hidden="true"><span class="fx-fill"></span><span class="fx-ripple"></span></span><span class="fx-label">${text}</span>${FX_ICON}<span class="fx-spinner" aria-hidden="true"></span></button>`;
}
export class ButtonPlayer {
  constructor(
    button,
    {
      records = [],
      task = false,
      label = "试一下",
      taskMs = TASK_MS,
      taskMode = "demo",
      hostManaged = false,
      onStatus = () => {},
      onResult = () => {},
      clock = { set: setTimeout, clear: clearTimeout },
    } = {},
  ) {
    this.button = button;
    this.records = records;
    this.task = task;
    this.label = label;
    this.taskMs = taskMs;
    this.taskMode = taskMode;
    this.hostManaged = hostManaged;
    this.onStatus = onStatus;
    this.onResult = onResult;
    this.clock = clock;
    this.timers = new Map();
    this.animations = new Set();
    this.listeners = [];
    this.generation = 0;
    this.phase = "idle";
    this.count = 0;
    this.attempt = 0;
    this.speed = 1;
    this.destroyed = false;
    this.media = matchMedia("(prefers-reduced-motion: reduce)");
    this.listen(button, "pointerdown", (event) => {
      if (event.button !== 0 || button.disabled || this.phase === "loading") return;
      this.press();
      try {
        button.setPointerCapture(event.pointerId);
      } catch {}
    });
    this.listen(button, "pointerup", () => this.release());
    this.listen(button, "pointercancel", () => this.release());
    this.listen(button, "lostpointercapture", () => this.release());
    this.listen(button, "keydown", (event) => {
      if (
        event.isComposing ||
        event.ctrlKey ||
        event.altKey ||
        event.metaKey ||
        event.repeat
      )
        return;
      if ([" ", "Enter"].includes(event.key)) this.press();
    });
    this.listen(button, "keyup", (event) => {
      if ([" ", "Enter"].includes(event.key)) this.release();
    });
    this.listen(button, "blur", () => this.release());
    this.listen(button, "click", (event) => this.trigger(event), hostManaged);
    this.motionChanged = () => {
      this.cancelAnimations();
      button.classList.remove("fx-playing", "fx-releasing");
      this.updateVariables();
    };
    this.media.addEventListener?.("change", this.motionChanged);
    this.visibilityChanged = () => {
      if (document.visibilityState === "hidden") {
        if (this.hostManaged) { this.release(); this.cancelAnimations(); }
        else this.reset();
      }
    };
    this.listen(document, "visibilitychange", this.visibilityChanged);
    this.updateVariables();
    this.emit("idle", "准备就绪，可以反复试用。");
  }
  listen(target, event, fn, capture = false) {
    target.addEventListener(event, fn, capture);
    this.listeners.push(() => target.removeEventListener(event, fn, capture));
  }
  record(trigger) {
    return this.records.find((record) => record.trigger === trigger);
  }
  params(record) {
    const effect = effectById(record.effectId);
    return normalizeEffectParams(
      effect,
      record.params || recommendedParams(effect),
    );
  }
  duration(record) {
    return Math.round(this.params(record).duration / this.speed);
  }
  schedule(key, callback, ms) {
    this.clearTimer(key);
    const generation = this.generation;
    const timer = this.clock.set(() => {
      this.timers.delete(key);
      if (!this.destroyed && generation === this.generation) callback();
    }, ms);
    this.timers.set(key, timer);
  }
  clearTimer(key) {
    if (this.timers.has(key)) {
      this.clock.clear(this.timers.get(key));
      this.timers.delete(key);
    }
  }
  emit(phase, text) {
    this.phase = phase;
    this.button.dataset.phase = phase;
    this.onStatus({ phase, text, count: this.count });
  }
  updateVariables() {
    const button = this.button;
    for (const [trigger, attribute] of [
      ["press", "pressEffect"],
      ["click", "clickEffect"],
    ])
      button.dataset[attribute] =
        effectById(this.record(trigger)?.effectId)?.kind || "none";
    const press = this.record("press");
    if (press) {
      const p = this.params(press);
      button.style.setProperty(
        "--fx-press-duration",
        `${p.duration / this.speed}ms`,
      );
      button.style.setProperty("--fx-scale", p.scale ?? 0.95);
      button.style.setProperty("--fx-bounce", 1 + (p.spring ?? 4) / 100);
      button.style.setProperty("--fx-depth", `${p.depth ?? 4}px`);
    }
    const click = this.record("click");
    if (click) {
      const p = this.params(click);
      button.style.setProperty("--fx-duration", `${p.duration / this.speed}ms`);
      button.style.setProperty("--fx-spread", `${p.spread ?? 8}px`);
      button.style.setProperty("--fx-glow-alpha", (p.intensity ?? 35) / 100);
      button.style.setProperty("--fx-distance", `${p.distance ?? 6}px`);
      button.style.setProperty(
        "--fx-direction",
        p.direction === 1 ? "-100%" : "100%",
      );
    }
    const waiting = this.record("loading");
    button.dataset.waitingEffect = waiting ? "selected" : "basic";
    button.style.setProperty(
      "--fx-loading-duration",
      `${waiting ? this.params(waiting).duration / this.speed : 180 / this.speed}ms`,
    );
    button.style.setProperty(
      "--fx-spin",
      `${waiting ? this.params(waiting).spin / this.speed : 800 / this.speed}ms`,
    );
    button.dataset.motion = this.media.matches ? "reduced" : "full";
  }
  setRecords(records) {
    this.reset();
    this.records = records;
    this.updateVariables();
  }
  setSpeed(speed) {
    this.reset();
    this.speed = Math.min(2, Math.max(0.5, Number(speed) || 1));
    this.updateVariables();
  }
  press() {
    if (this.destroyed || this.button.disabled || this.phase === "loading" || !this.record("press"))
      return;
    this.clearTimer("press");
    this.clearTimer("press-release");
    this.button.classList.remove("fx-releasing");
    this.button.classList.add("fx-pressed");
    this.onStatus({
      phase: "pressed",
      text: "已按下，松开后观察恢复。",
      count: this.count,
    });
  }
  release() {
    if (!this.button.classList.contains("fx-pressed")) return;
    this.button.classList.remove("fx-pressed");
    const record = this.record("press");
    if (!record) return;
    if (effectById(record.effectId).kind === "spring" && !this.media.matches) {
      this.button.classList.remove("fx-releasing");
      void this.button.offsetWidth;
      this.button.classList.add("fx-releasing");
      this.schedule(
        "press",
        () => this.button.classList.remove("fx-releasing"),
        this.duration(record),
      );
    }
  }
  playVisual(event) {
    if (this.hostManaged) this.ensureDecoration();
    const record = this.record("click");
    if (!record) return;
    const effect = effectById(record.effectId),
      p = this.params(record),
      button = this.button;
    this.cancelAnimations();
    button.classList.remove("fx-playing");
    const rect = button.getBoundingClientRect();
    const x =
      event?.detail > 0
        ? Math.max(0, Math.min(rect.width, event.clientX - rect.left))
        : rect.width / 2;
    const y =
      event?.detail > 0
        ? Math.max(0, Math.min(rect.height, event.clientY - rect.top))
        : rect.height / 2;
    const size =
      (Math.max(rect.width || 152, rect.height || 48) * (p.range ?? 240)) / 100;
    const ripple = button.querySelector(".fx-ripple");
    ripple.style.width = size + "px";
    ripple.style.height = size + "px";
    ripple.style.left = x + "px";
    ripple.style.top = y + "px";
    if (!this.media.matches) {
      void button.offsetWidth;
      button.classList.add("fx-playing");
      if (effect.kind === "icon" && button.querySelector(".fx-icon").animate) {
        const animation = button
          .querySelector(".fx-icon")
          .animate(
            [
              { transform: "translateX(0)" },
              { transform: `translateX(${p.distance}px)`, offset: 0.45 },
              { transform: "translateX(0)" },
            ],
            { duration: this.duration(record), easing: "ease-out" },
          );
        this.animations.add(animation);
        animation.onfinish = () => this.animations.delete(animation);
      }
    }
    this.schedule(
      "visual",
      () => {
        button.classList.remove("fx-playing");
        this.cancelAnimations();
      },
      this.duration(record),
    );
  }
  trigger(event, { replay = false } = {}) {
    if (this.destroyed || (this.hostManaged && (this.button.disabled || this.button.getAttribute('aria-disabled') === 'true'))) return false;
    if (this.phase === "loading" && !replay) {
      this.onStatus({
        phase: "loading",
        text: "同一模拟任务进行中，连续点击已忽略。",
        count: this.count,
      });
      return false;
    }
    if (replay) this.reset();
    this.count++;
    this.release();
    this.playVisual(event);
    if (replay || (!event && this.record("press"))) {
      this.press();
      this.schedule("press-release", () => this.release(), 80 / this.speed);
    }
    const taskRecord = this.records.some((r) =>
      ["loading", "success", "error"].includes(r.trigger),
    );
    if (!this.hostManaged && (this.task || taskRecord)) {
      this.startTask();
      return true;
    }
    this.emit(
      "clicked",
      `已响应第 ${this.count} 次操作。${this.media.matches ? "系统减少动效已启用，保留静态反馈。" : ""}`,
    );
    this.onResult({ phase: "clicked" });
    return true;
  }
  startTask() {
    this.clearTimer("result");
    this.attempt++;
    this.button.classList.remove(
      "fx-success",
      "fx-error",
      "fx-completing",
      "fx-complete",
    );
    this.button.classList.add("fx-loading");
    this.button.setAttribute("aria-busy", "true");
    this.button.setAttribute("aria-disabled", "true");
    this.button.querySelector(".fx-label").textContent = "处理中";
    this.emit(
      "loading",
      this.taskMode === "external"
        ? "正在等待业务请求返回结果。"
        : `模拟任务进行中，${Math.round(this.taskMs / this.speed)}ms 后返回结果。`,
    );
    if (this.taskMode === "external") return;
    const fails = Boolean(this.record("error")) && this.attempt === 1;
    this.schedule(
      "task",
      () => this.finishTask(fails),
      this.taskMs / this.speed,
    );
  }
  finishTask(fails) {
    if (this.destroyed || this.phase !== "loading") return;
    this.clearTimer("task");
    const button = this.button;
    button.classList.remove("fx-loading");
    button.removeAttribute("aria-busy");
    button.removeAttribute("aria-disabled");
    const result = this.record(fails ? "error" : "success"),
      params = result ? this.params(result) : null;
    button.style.setProperty(
      "--fx-result-duration",
      `${params ? params.duration / this.speed : 180}ms`,
    );
    button.style.setProperty(
      "--fx-error-distance",
      `${params?.distance ?? 3}px`,
    );
    button.classList.add(
      fails
        ? "fx-error"
        : this.record("success")
          ? "fx-success"
          : "fx-complete",
    );
    if (!this.media.matches) button.classList.add("fx-completing");
    button.querySelector(".fx-label").textContent = fails
      ? "重试"
      : this.record("success")
        ? "已完成"
        : "完成";
    this.emit(
      fails ? "error" : "success",
      fails
        ? "模拟失败，点击“重试”可恢复；输入与选择继续保留。"
        : "模拟任务已完成，结果来自这次任务结束。",
    );
    this.onResult({ phase: fails ? "error" : "success" });
    this.schedule(
      "result-animation",
      () => button.classList.remove("fx-completing"),
      params ? params.duration / this.speed : 180,
    );
    if (!fails)
      this.schedule(
        "result",
        () => {
          button.classList.remove("fx-success", "fx-complete");
          button.querySelector(".fx-label").textContent = this.label;
        },
        (params?.hold ?? 1400) / this.speed,
      );
  }
  cancelAnimations() {
    this.animations.forEach((animation) => animation.cancel());
    this.animations.clear();
  }
  // Attach decoration without replacing business-owned text, nodes or handlers.
  ensureDecoration() {
    if (!this.button.querySelector('.fx-clip')) {
      const clip = document.createElement('span');
      clip.className = 'fx-clip';
      clip.setAttribute('aria-hidden', 'true');
      const ripple = document.createElement('span');
      ripple.className = 'fx-ripple';
      clip.append(ripple);
      this.button.append(clip);
    }
    if (!this.button.querySelector('.fx-spinner')) {
      const spinner = document.createElement('span');
      spinner.className = 'fx-spinner';
      spinner.setAttribute('aria-hidden', 'true');
      this.button.append(spinner);
    }
  }
  setBusy(running) {
    if (!this.hostManaged || this.destroyed) return;
    this.ensureDecoration();
    this.button.classList.toggle('fx-loading', Boolean(running));
    this.phase = running ? 'loading' : 'idle';
    this.button.dataset.phase = this.phase;
  }
  reset() {
    this.generation++;
    for (const timer of this.timers.values()) this.clock.clear(timer);
    this.timers.clear();
    this.cancelAnimations();
    this.button.classList.remove(
      "fx-pressed",
      "fx-releasing",
      "fx-playing",
      "fx-loading",
      "fx-success",
      "fx-complete",
      "fx-error",
      "fx-completing",
    );
    if (!this.hostManaged) {
      this.button.removeAttribute("aria-busy");
      this.button.removeAttribute("aria-disabled");
      this.button.querySelector(".fx-label").textContent = this.label;
    }
    this.attempt = 0;
    this.emit("idle", "已重置，可以再次试用。");
  }
  destroy() {
    this.reset();
    this.destroyed = true;
    this.listeners.forEach((remove) => remove());
    this.listeners = [];
    this.media.removeEventListener?.("change", this.motionChanged);
  }
}

export function attachButton(button, { records = [] } = {}) {
  const variables = button.getAttribute('style');
  button.classList.add('fx-attached');
  const player = new ButtonPlayer(button, { records, hostManaged: true });
  player.ensureDecoration();
  const destroy = player.destroy.bind(player);
  player.destroy = () => {
    if (player.destroyed) return;
    destroy();
    button.classList.remove('fx-attached');
    button.querySelectorAll('.fx-clip,.fx-spinner').forEach(node => node.remove());
    for (const key of [...button.style]) if (key.startsWith('--fx-')) button.style.removeProperty(key);
    // Restore only effect variables, retaining business-owned styles changed since mounting.
    const original = document.createElement('span');
    original.setAttribute('style', variables || '');
    for (const key of [...original.style]) if (key.startsWith('--fx-')) button.style.setProperty(key, original.style.getPropertyValue(key));
    for (const key of ['phase','pressEffect','clickEffect','waitingEffect','motion']) delete button.dataset[key];
  };
  return player;
}
