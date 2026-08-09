# Browser extension security boundary

The Skeinix browser extension controls only the tab explicitly connected to an
authenticated Skeinix Chat. Its broad Chrome permissions are required for the
browser-automation contract:

- `debugger` sends Chrome DevTools Protocol commands for navigation, DOM input,
  screenshots, and network-aware waits.
- `tabs` identifies and observes the user-selected controlled tab.
- `scripting` installs the isolated-world bridge used to inspect page state.
- `<all_urls>` lets that isolated bridge operate on the site selected by the
  user; it does not expose an extension resource to arbitrary pages.
- `sidePanel` hosts the authenticated Skeinix Chat interface.
- `offscreen` keeps the authenticated transport alive when Chrome suspends the
  visible side panel.
- `storage` retains device-local extension settings and scoped session state.

Production builds generate exact host permissions and
`externally_connectable` origins from the configured Skeinix application
origin. The service worker validates every external sender again at runtime,
and controlled-tab state is cleared with the browser session.

Any permission change requires boundary-test coverage and a manual privacy and
security review before an extension-store release.
