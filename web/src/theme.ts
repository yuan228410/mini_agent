const STORAGE_KEY = 'mini-ai-theme'

export type Theme = 'light' | 'dark'

function getSystemPreference(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function initTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY) as Theme | null
  const theme = stored || getSystemPreference()
  applyTheme(theme)
  return theme
}

export function applyTheme(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme)
  localStorage.setItem(STORAGE_KEY, theme)
}

export function toggleTheme(current: Theme): Theme {
  const next = current === 'light' ? 'dark' : 'light'
  applyTheme(next)
  return next
}
