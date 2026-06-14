const STORAGE_KEY = 'mini-ai-theme'

export const THEMES = [
  { id: 'linen-light', name: '亚麻', mode: 'light', icon: '☼', accent: '#E8912D', description: '温暖纸感，柔和耐看' },
  { id: 'paper-light', name: '素纸', mode: 'light', icon: '◌', accent: '#4F7CAC', description: '清爽中性，专注阅读' },
  { id: 'sage-light', name: '鼠尾草', mode: 'light', icon: '✦', accent: '#6E8B74', description: '绿灰低饱和，安静自然' },
  { id: 'porcelain-light', name: '瓷白', mode: 'light', icon: '◇', accent: '#4E7FB9', description: '蓝灰瓷感，简洁通透' },
  { id: 'graphite-dark', name: '石墨', mode: 'dark', icon: '◐', accent: '#F0A030', description: '暖调暗色，沉稳经典' },
  { id: 'midnight-dark', name: '午夜', mode: 'dark', icon: '☾', accent: '#7AA7FF', description: '深蓝夜色，层次清晰' },
  { id: 'ember-dark', name: '余烬', mode: 'dark', icon: '◆', accent: '#F07F4F', description: '炭黑暖橙，工程质感' },
  { id: 'slate-dark', name: '板岩', mode: 'dark', icon: '◒', accent: '#8AA7C7', description: '蓝灰暗色，低调舒适' },
] as const

export type Theme = typeof THEMES[number]['id']
export type ThemeMode = typeof THEMES[number]['mode']
export type ThemeMeta = typeof THEMES[number]

const DEFAULT_LIGHT_THEME: Theme = 'linen-light'
const DEFAULT_DARK_THEME: Theme = 'graphite-dark'
const LEGACY_THEME_MAP: Record<string, Theme> = {
  light: DEFAULT_LIGHT_THEME,
  dark: DEFAULT_DARK_THEME,
}

function isTheme(value: string | null): value is Theme {
  return THEMES.some(theme => theme.id === value)
}

function getSystemPreference(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? DEFAULT_DARK_THEME : DEFAULT_LIGHT_THEME
}

export function normalizeTheme(value: string | null): Theme {
  if (isTheme(value)) return value
  if (value && LEGACY_THEME_MAP[value]) return LEGACY_THEME_MAP[value]
  return getSystemPreference()
}

export function getThemeMeta(theme: Theme): ThemeMeta {
  return THEMES.find(item => item.id === theme) || THEMES[0]
}

export function initTheme(): Theme {
  const theme = normalizeTheme(localStorage.getItem(STORAGE_KEY))
  applyTheme(theme)
  return theme
}

export function applyTheme(theme: Theme) {
  const meta = getThemeMeta(theme)
  document.documentElement.setAttribute('data-theme', theme)
  document.documentElement.setAttribute('data-theme-mode', meta.mode)
  localStorage.setItem(STORAGE_KEY, theme)
}

export function setTheme(theme: Theme): Theme {
  applyTheme(theme)
  return theme
}

export function nextTheme(current: Theme): Theme {
  const idx = THEMES.findIndex(theme => theme.id === current)
  const next = THEMES[(idx + 1) % THEMES.length]?.id || DEFAULT_LIGHT_THEME
  applyTheme(next)
  return next
}
