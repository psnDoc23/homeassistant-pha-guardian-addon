# Guardian Local development workflow

Guardian Local uses two development loops.

## Fast loop: Replit preview

The `Guardian Local Preview` workflow runs the real FastAPI add-on and dashboard
against a scenario-driven mock Home Assistant Supervisor.

Available scenarios:

- Healthy home
- Repeated device dropout
- Correlated dropout
- Missed automation
- A check can't finish (nothing found, but the result must not read as healthy)

Changes to Python, HTML, CSS, and JavaScript reload automatically. This loop is
used for interface work, issue presentation, interactions, responsive behavior,
and most diagnostic development.

Run the automated add-on checks from the repository root:

```bash
cd guardian-addon/pha_guardian
pytest -q checkers
```

## Integration loop: development Home Assistant

Replit cannot run Docker or Home Assistant Supervisor. Supervisor permissions,
Ingress behavior, installation, upgrades, and real entity data must be checked
on a separate development Home Assistant instance.

The add-on is published from this monorepo to its standalone repository with:

```bash
git subtree push --prefix=guardian-addon addon-origin main
```

For development, use a dedicated branch in the standalone repository and a
local/development add-on installation in Home Assistant. The expected cycle is:

1. Complete and verify a milestone in the Replit preview.
2. Push the `guardian-addon` subtree to the development branch.
3. Pull or copy the add-on into the development Home Assistant instance.
4. Rebuild and restart the local add-on.
5. Verify Ingress, Supervisor access, live diagnostics, and logs.
6. Record any environment-specific differences before the public release.

The exact pull/rebuild commands depend on how the development Home Assistant
instance is hosted. Keep public version bumps for release candidates rather
than every Replit preview change.
## Local-first behavior (0.0.179+)

- The app screen works with no website account and no token. Requests that arrive
  through Home Assistant's Ingress (from `172.30.32.2`) are trusted; every other
  caller needs the `api_token` as a Bearer token.
- Nothing is sent to the website unless the `website_sharing` option is on **and**
  an `api_token` is set. A token alone does not start sharing.
- A check that cannot finish is reported as incomplete, never as healthy.
  `check_report.py` decides this; the screen and the website push both use it.
- `/issues` keeps its original `issues` list and adds `report`, `history`, and `sharing`.
