# Playwright E2E specs

Critical-journey coverage for the workflow canvas. Each spec is independently runnable:
no spec leans on data created by another. The fixtures module
(`fixtures.ts`) provides API-level helpers (createWorkflow,
seedStartNode, etc.) so each test seeds + cleans up its own data.

| File | Coverage | Skip condition |
|---|---|---|
| `01-login-and-workspace.spec.ts` | Paste-token login + `/workspace` lands | (none) |
| `02-create-workflow.spec.ts` | "+ New workflow" modal + redirect to `/workflow/{wfId}` | (none) |
| `03-chat-stream.spec.ts` | Send chat turn + assistant bubble appears | `!AGENT_API_KEY` |
| `04-execute-node.spec.ts` | Execute toolbar → inspector status flow | (none) |
| `05-delete-with-confirmation.spec.ts` | Card menu → typed-name destructive delete | (none) |
| `06-a11y.spec.ts` | `axe-core` scan on `/workspace` + `/workflow/{wfId}` | (none) |

## Running

Requires:
- `VIBECANVAS_API_DEV_TOKEN` exported (defaults to `'dev-token'`).
- `AGENT_API_KEY` exported if you want spec #3 to run instead of skip.
- Python + uv set up in `../api/` (auto-installed in the conda env).
- `pnpm install` already done in `web/`.

```bash
pnpm test:e2e          # headless, all specs
pnpm test:e2e:headed   # headed (debug)
```

`playwright.config.ts` boots both backend (uvicorn) and frontend (`pnpm
dev`) via `webServer`, then runs the specs against `http://localhost:5173`.

## Author notes

- Use `data-action="<verb>"` (or `data-role="<thing>"`) for stable selectors
  rather than text — the i18n layer can drift, attributes stay constant.
- Use the API fixtures (`createWorkflow`, `seedStartNode`, etc.) instead
  of driving the UI to set up state — the UI path is what the test is
  verifying, not what it's pre-seeding.
- Always wrap mutating fixtures in `try/finally` and delete the resource
  in `finally` so a failing assertion doesn't leak state to the next run.
