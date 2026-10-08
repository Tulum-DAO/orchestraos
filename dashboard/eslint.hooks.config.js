// CI gate for ONE rule: react-hooks/rules-of-hooks. The full `npm run lint` still has older
// findings (mostly no-explicit-any), so it cannot gate yet; a hook called conditionally can.
// It is exactly the class behind operator finding #9: `useMatch(a) || useMatch(b)` in
// DashboardLayout changed the hook count between routes and blacked out the whole app.
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: { parser: tseslint.parser },
    plugins: { 'react-hooks': reactHooks },
    rules: { 'react-hooks/rules-of-hooks': 'error' },
  },
])
