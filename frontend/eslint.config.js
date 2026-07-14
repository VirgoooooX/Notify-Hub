import js from '@eslint/js'
import vue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'
export default tseslint.config(
  { ignores: ['dist', 'coverage'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...vue.configs['flat/recommended'],
  { languageOptions: { globals: { window: 'readonly', localStorage: 'readonly', fetch: 'readonly', Headers: 'readonly', URLSearchParams: 'readonly', clearTimeout: 'readonly', Response: 'readonly' } } },
  { files: ['**/*.vue'], languageOptions: { parserOptions: { parser: tseslint.parser } }, rules: { 'vue/multi-word-component-names': 'off', 'vue/max-attributes-per-line': 'off', 'no-irregular-whitespace': 'off' } },
)
