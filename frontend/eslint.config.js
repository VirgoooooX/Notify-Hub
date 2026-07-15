import js from '@eslint/js'
import vue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'coverage'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    languageOptions: {
      globals: {
        window: 'readonly',
        localStorage: 'readonly',
        fetch: 'readonly',
        Headers: 'readonly',
        URLSearchParams: 'readonly',
        clearTimeout: 'readonly',
        Response: 'readonly',
        document: 'readonly',
        Event: 'readonly',
        HTMLInputElement: 'readonly',
        HTMLSelectElement: 'readonly',
        HTMLTextAreaElement: 'readonly',
        MouseEvent: 'readonly',
        KeyboardEvent: 'readonly',
        navigator: 'readonly',
        setTimeout: 'readonly',
        FocusEvent: 'readonly',
        Intl: 'readonly',
      }
    }
  },
  {
    files: ['**/*.vue', '**/*.ts'],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser
      }
    },
    rules: {
      'vue/multi-word-component-names': 'off',
      'vue/max-attributes-per-line': 'off',
      'no-irregular-whitespace': 'off',
      'vue/no-mutating-props': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': 'off',
      'vue/require-default-prop': 'off',
    }
  },
)
