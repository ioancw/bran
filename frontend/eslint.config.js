// Flat ESLint config: JS/TS recommended + Svelte recommended, with
// eslint-config-prettier last so no rule fights the formatter. Correctness
// rules only — formatting is Prettier's job.
import js from '@eslint/js'
import ts from 'typescript-eslint'
import svelte from 'eslint-plugin-svelte'
import prettier from 'eslint-config-prettier'
import globals from 'globals'
import svelteConfig from './svelte.config.js'

export default ts.config(
  js.configs.recommended,
  ...ts.configs.recommended,
  ...svelte.configs.recommended,
  prettier,
  ...svelte.configs.prettier,
  {
    languageOptions: {
      globals: { ...globals.browser },
    },
    rules: {
      // The codebase deliberately swallows some errors with a comment inside
      // the catch; require the comment, not a throwaway binding.
      'no-empty': ['error', { allowEmptyCatch: true }],
      // `void promise` is the project's "fire and forget" idiom.
      '@typescript-eslint/no-floating-promises': 'off',
      // Underscore-prefixed = intentionally unused (e.g. regex replace args).
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' },
      ],
    },
  },
  {
    files: ['**/*.svelte', '**/*.svelte.ts'],
    languageOptions: {
      parserOptions: {
        parser: ts.parser,
        extraFileExtensions: ['.svelte'],
        svelteConfig,
      },
    },
  },
  {
    ignores: ['dist/', 'node_modules/', '../src/bran/web/spa/**'],
  },
)
