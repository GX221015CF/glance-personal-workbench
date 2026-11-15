// Selected snapshot. Update with node tools/sync-wechat-effects.mjs
export default {
  "revision": 58,
  "records": [
    {
      "component": "button",
      "trigger": "press",
      "scene": "general",
      "effectId": "button-press-soft",
      "effectVersion": 1,
      "params": {
        "duration": 150,
        "scale": 0.96
      }
    },
    {
      "component": "button",
      "trigger": "click",
      "scene": "general",
      "effectId": "button-click-ripple",
      "effectVersion": 1,
      "params": {
        "duration": 480,
        "range": 240
      }
    },
    {
      "component": "button",
      "trigger": "loading",
      "scene": "refresh",
      "effectId": "button-wait-spinner",
      "effectVersion": 1,
      "params": {
        "duration": 180,
        "spin": 800
      }
    },
    {
      "component": "accordion",
      "trigger": "expand",
      "scene": "general",
      "effectId": "accordion-composed",
      "effectVersion": 1,
      "params": {
        "duration": 340
      }
    }
  ]
};
