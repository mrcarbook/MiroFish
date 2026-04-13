import { createI18n } from 'vue-i18n'
import languages from '../../../locales/languages.json'

const localeFiles = import.meta.glob('../../../locales/!(languages).json', { eager: true })

const messages = {}
const availableLocales = []

for (const path in localeFiles) {
  const key = path.match(/\/([^/]+)\.json$/)[1]
  if (languages[key]) {
    messages[key] = localeFiles[path].default
    availableLocales.push({ key, label: languages[key].label })
  }
}

// Use versioned key so old 'zh' default doesn't override Italian default
const LOCALE_KEY = 'locale_v2'
const DEFAULT_LOCALE = 'it'
const savedLocale = localStorage.getItem(LOCALE_KEY) || DEFAULT_LOCALE

// Migrate old key: if user had explicitly set a non-default locale, preserve it
const legacyLocale = localStorage.getItem('locale')
const resolvedLocale = localStorage.getItem(LOCALE_KEY)
  ? savedLocale
  : (legacyLocale && legacyLocale !== 'zh' && messages[legacyLocale])
    ? legacyLocale
    : DEFAULT_LOCALE

// Save resolved locale with new key and clean up old one
localStorage.setItem(LOCALE_KEY, resolvedLocale)
localStorage.removeItem('locale')

const i18n = createI18n({
  legacy: false,
  locale: resolvedLocale,
  fallbackLocale: 'en',
  messages
})

export { availableLocales }
export default i18n
