// Flat config do template Vite react-ts (plano 05-05, Task 1).
// eslint e tsc são obrigatórios no CI (arquitetura §Ferramentas).
// Código GERADO não passa no lint: src/api/generated/ tem a MESMA exclusão
// do cheque de desatualização (npm run generate-types + git diff).
// @eslint/js não entra: o ESLint 10 não o embarca e o pacote não está na
// lista aprovada — para um projeto 100% TS, o recommended do typescript-eslint
// substitui o recommended do core (overrides de eslint-recommended).
import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default defineConfig([
  globalIgnores(['dist', 'src/api/generated']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
  },
])
