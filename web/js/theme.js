/* NPL · 主题与强调色
 * 主题预设提供整套「外壳 + 内容」配色（深浅各有几套）；强调色在此之上单独覆盖。
 * 计算出的变量同时写入 localStorage，供 <head> 里的内联脚本在首屏前应用，避免闪一下默认配色。
 */

export const DEFAULT_ACCENT = "#0e7c86";
export const DEFAULT_THEME = "midnight";

export const ACCENT_PRESETS = [
  { name: "青绿", value: "#0e7c86" },
  { name: "海蓝", value: "#2563eb" },
  { name: "紫罗兰", value: "#7c3aed" },
  { name: "琥珀", value: "#b45309" },
  { name: "玫瑰", value: "#be123c" },
  { name: "森林", value: "#15803d" },
];

/* 外壳 = 侧栏 / 标题栏 / 阅读器顶栏 / 引注页顶栏；内容 = 页面底、卡片、文字、线条。 */
export const THEME_PRESETS = [
  {
    key: "midnight", name: "深海", dark: true, accent: "#0e7c86",
    shell: { bg: "#0b2136", mid: "#0a1a2b", bg2: "#071523", bg3: "#0d2033", text: "#dbe7f3", strong: "#ffffff", muted: "#7f9ab5", faint: "#5d7893", nav: "#a9bfd4", navStrong: "#e8f1fa", pill: "#9fb6ca", chrome: "#cfe0f0", line: "rgba(255,255,255,.07)", lineStrong: "rgba(255,255,255,.10)", hover: "rgba(255,255,255,.05)", hoverStrong: "rgba(255,255,255,.10)", hoverActive: "rgba(255,255,255,.16)", menu: "#12283d", danger: "#ffb4ac", activeText: "#ffffff" },
    content: { ink: "#10203a", ink2: "#44566f", ink3: "#5f7186", bg: "#f4f6f9", surface: "#ffffff", surface2: "#f8fafc", surface3: "#f1f5f9", line: "#e2e8f0", lineSoft: "#eef2f7", lineStrong: "#cbd5e1" },
  },
  {
    key: "graphite", name: "石墨", dark: true, accent: "#2563eb",
    shell: { bg: "#1f2329", mid: "#1a1d22", bg2: "#141619", bg3: "#23272e", text: "#e6e8ec", strong: "#ffffff", muted: "#9aa3b0", faint: "#6f7885", nav: "#b9c2cf", navStrong: "#f2f5f9", pill: "#9aa3b0", chrome: "#d7dce3", line: "rgba(255,255,255,.08)", lineStrong: "rgba(255,255,255,.12)", hover: "rgba(255,255,255,.06)", hoverStrong: "rgba(255,255,255,.12)", hoverActive: "rgba(255,255,255,.18)", menu: "#262b33", danger: "#ffb4ac", activeText: "#ffffff" },
    content: { ink: "#17202b", ink2: "#4a5665", ink3: "#627086", bg: "#f2f3f5", surface: "#ffffff", surface2: "#f7f8fa", surface3: "#eef0f3", line: "#dfe3e8", lineSoft: "#eceef1", lineStrong: "#c6cdd6" },
  },
  {
    key: "violet", name: "紫夜", dark: true, accent: "#7c3aed",
    shell: { bg: "#1a1526", mid: "#141020", bg2: "#0b0910", bg3: "#1f1830", text: "#efecf7", strong: "#ffffff", muted: "#a9a2be", faint: "#726b85", nav: "#b9b2cc", navStrong: "#f4f1fb", pill: "#a9a2be", chrome: "#e0dcf0", line: "rgba(255,255,255,.08)", lineStrong: "rgba(255,255,255,.12)", hover: "rgba(255,255,255,.06)", hoverStrong: "rgba(255,255,255,.12)", hoverActive: "rgba(255,255,255,.18)", menu: "#241b36", danger: "#ffb4ac", activeText: "#ffffff" },
    content: { ink: "#221d30", ink2: "#564c6b", ink3: "#74698b", bg: "#f6f4fa", surface: "#ffffff", surface2: "#faf8fd", surface3: "#f1edf8", line: "#e6e0f0", lineSoft: "#efeaf7", lineStrong: "#d3c9e6" },
  },
  {
    key: "paper", name: "纸白", dark: false, accent: "#0e7c86",
    shell: { bg: "#eef2f7", mid: "#e9eef5", bg2: "#e3e9f1", bg3: "#f3f6fa", text: "#1b2a3a", strong: "#0f1c2b", muted: "#66788c", faint: "#8595a8", nav: "#44566f", navStrong: "#10203a", pill: "#5d7186", chrome: "#31465c", line: "rgba(15,23,42,.10)", lineStrong: "rgba(15,23,42,.16)", hover: "rgba(15,23,42,.05)", hoverStrong: "rgba(15,23,42,.08)", hoverActive: "rgba(15,23,42,.12)", menu: "#ffffff", danger: "#b91c1c", activeText: "#0b4f55" },
    content: { ink: "#10203a", ink2: "#44566f", ink3: "#5f7186", bg: "#f4f6f9", surface: "#ffffff", surface2: "#f8fafc", surface3: "#f1f5f9", line: "#e2e8f0", lineSoft: "#eef2f7", lineStrong: "#cbd5e1" },
  },
  {
    key: "cream", name: "米白", dark: false, accent: "#b45309",
    shell: { bg: "#efe8dd", mid: "#ebe2d5", bg2: "#e5dbcb", bg3: "#f5efe6", text: "#2a241c", strong: "#1c1711", muted: "#7a6d5c", faint: "#958876", nav: "#5c5040", navStrong: "#2a241c", pill: "#6b6153", chrome: "#4a4032", line: "rgba(42,36,28,.12)", lineStrong: "rgba(42,36,28,.18)", hover: "rgba(42,36,28,.05)", hoverStrong: "rgba(42,36,28,.09)", hoverActive: "rgba(42,36,28,.14)", menu: "#fffdf8", danger: "#b91c1c", activeText: "#6b3f05" },
    content: { ink: "#2a241c", ink2: "#6b6153", ink3: "#746958", bg: "#f7f3ec", surface: "#fffdf8", surface2: "#fffefb", surface3: "#f2ece1", line: "#e6dccd", lineSoft: "#f1e9dd", lineStrong: "#d5c7b3" },
  },
  {
    key: "celadon", name: "青瓷", dark: false, accent: "#0f766e",
    shell: { bg: "#e2ede8", mid: "#dce9e3", bg2: "#d3e3dc", bg3: "#eef4f1", text: "#17231f", strong: "#0d1714", muted: "#6a7d76", faint: "#87988f", nav: "#3f544c", navStrong: "#17231f", pill: "#4e625b", chrome: "#2f4038", line: "rgba(23,35,31,.10)", lineStrong: "rgba(23,35,31,.16)", hover: "rgba(23,35,31,.05)", hoverStrong: "rgba(23,35,31,.08)", hoverActive: "rgba(23,35,31,.12)", menu: "#ffffff", danger: "#b91c1c", activeText: "#0b4f45" },
    content: { ink: "#17231f", ink2: "#4e625b", ink3: "#5e7169", bg: "#edf4f1", surface: "#ffffff", surface2: "#f5faf8", surface3: "#e4efea", line: "#d8e6df", lineSoft: "#e9f2ee", lineStrong: "#c2d6cc" },
  },
  {
    key: "sakura", name: "樱粉", dark: false, accent: "#db2777",
    shell: { bg: "#f7e9ef", mid: "#f3e1e9", bg2: "#eed9e3", bg3: "#fbf1f5", text: "#2b1f24", strong: "#1d1317", muted: "#8a6f7b", faint: "#a38794", nav: "#5c4652", navStrong: "#2b1f24", pill: "#6e5a63", chrome: "#4a3540", line: "rgba(43,31,36,.10)", lineStrong: "rgba(43,31,36,.16)", hover: "rgba(43,31,36,.05)", hoverStrong: "rgba(43,31,36,.08)", hoverActive: "rgba(43,31,36,.12)", menu: "#ffffff", danger: "#b91c1c", activeText: "#9d174d" },
    content: { ink: "#2b1f24", ink2: "#6e5a63", ink3: "#7e6772", bg: "#fdf4f7", surface: "#ffffff", surface2: "#fffafc", surface3: "#f8eaf1", line: "#efdde6", lineSoft: "#f7e9ef", lineStrong: "#e2c8d5" },
  },
  {
    key: "daylight", name: "日光", dark: false, accent: "#2563eb",
    shell: { bg: "#e8eefb", mid: "#e1e9f9", bg2: "#d8e2f6", bg3: "#f1f5fd", text: "#17233a", strong: "#0d1830", muted: "#64748b", faint: "#8494ac", nav: "#44566f", navStrong: "#10203a", pill: "#5b6b84", chrome: "#33445c", line: "rgba(23,35,58,.10)", lineStrong: "rgba(23,35,58,.16)", hover: "rgba(23,35,58,.05)", hoverStrong: "rgba(23,35,58,.08)", hoverActive: "rgba(23,35,58,.12)", menu: "#ffffff", danger: "#b91c1c", activeText: "#1d4ed8" },
    content: { ink: "#17233a", ink2: "#4a5a72", ink3: "#607086", bg: "#f4f6fb", surface: "#ffffff", surface2: "#f8faff", surface3: "#eef2fa", line: "#e0e7f3", lineSoft: "#ecf1f9", lineStrong: "#c9d4e6" },
  },
];

const STORAGE_KEY = "pw.appearance.vars";
const LEGACY_KEY = "pw.accent.vars";
const HEX_RE = /^#[0-9a-fA-F]{6}$/;

export function normalizeAccent(value) {
  const text = String(value || "").trim();
  return HEX_RE.test(text) ? text.toLowerCase() : DEFAULT_ACCENT;
}

export function getTheme(key) {
  return THEME_PRESETS.find((preset) => preset.key === key) || THEME_PRESETS[0];
}

function hexToRgb(hex) {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

function rgbToHsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
    else if (max === g) h = ((b - r) / d + 2) / 6;
    else h = ((r - g) / d + 4) / 6;
  }
  return [h * 360, s * 100, l * 100];
}

function hslToHex(h, s, l) {
  h = ((h % 360) + 360) % 360;
  s = Math.max(0, Math.min(100, s)) / 100;
  l = Math.max(0, Math.min(100, l)) / 100;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const seg = [[c, x, 0], [x, c, 0], [0, c, x], [0, x, c], [x, 0, c], [c, 0, x]][Math.floor(h / 60) % 6];
  return "#" + seg.map((v) => Math.round((v + m) * 255).toString(16).padStart(2, "0")).join("");
}

export function computeAccentVars(value) {
  const accent = normalizeAccent(value);
  const [h, s, l] = rgbToHsl(hexToRgb(accent));
  return {
    "--accent": accent,
    "--accent-2": hslToHex(h, Math.min(100, s + 4), Math.min(64, l + 9)),
    "--accent-soft": hslToHex(h, Math.min(60, s * 0.55), 95),
    "--accent-line": hslToHex(h, Math.min(58, s * 0.5), 86),
    "--row-alt": hslToHex(h, Math.min(40, s * 0.36), 96),
    "--row-selected": hslToHex(h, Math.min(38, s * 0.34), 90),
  };
}

function themeVars(preset) {
  const s = preset.shell;
  const c = preset.content;
  return {
    "--shell-bg": s.bg, "--shell-bg-mid": s.mid, "--shell-bg-2": s.bg2, "--shell-bg-3": s.bg3,
    "--shell-text": s.text, "--shell-text-strong": s.strong, "--shell-muted": s.muted, "--shell-faint": s.faint,
    "--shell-nav": s.nav, "--shell-nav-strong": s.navStrong, "--shell-pill-text": s.pill,
    "--shell-chrome-text": s.chrome, "--shell-line": s.line, "--shell-line-strong": s.lineStrong,
    "--shell-hover": s.hover, "--shell-hover-strong": s.hoverStrong, "--shell-hover-active": s.hoverActive,
    "--shell-menu-bg": s.menu, "--shell-danger": s.danger, "--shell-active-text": s.activeText,
    "--ink": c.ink, "--ink-2": c.ink2, "--ink-3": c.ink3,
    "--bg": c.bg, "--surface": c.surface, "--surface-2": c.surface2, "--surface-3": c.surface3,
    "--line": c.line, "--line-soft": c.lineSoft, "--line-strong": c.lineStrong,
  };
}

export function applyAppearance({ theme, accent } = {}, { persist = true } = {}) {
  const preset = getTheme(theme);
  const vars = { ...themeVars(preset), ...computeAccentVars(accent || preset.accent) };
  const root = document.documentElement;
  for (const [name, color] of Object.entries(vars)) root.style.setProperty(name, color);
  if (persist) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(vars));
    } catch {
      /* 隐私模式下忽略 */
    }
  }
  return { preset, vars };
}

/* 兼容旧调用：只改强调色，主题保持当前缓存（没有则默认）。 */
export function applyAccent(value, { persist = true } = {}) {
  const cached = cachedAppearance();
  const theme = cached?.theme || DEFAULT_THEME;
  return applyAppearance({ theme, accent: value }, { persist }).vars;
}

export function cachedAppearance() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (raw && typeof raw === "object") return raw;
  } catch {
    /* 忽略损坏的缓存 */
  }
  return null;
}

export function cachedAccentVars() {
  try {
    const raw = JSON.parse(localStorage.getItem(LEGACY_KEY) || "null");
    return raw && typeof raw === "object" ? raw : null;
  } catch {
    return null;
  }
}
