# Flask/Jinja component catalog candidates

The catalog contains 10 small, original HTML/Jinja fragments targeting the
existing Flask/Jinja application: buttons, text/password inputs, card/layout,
login-card pattern, sidebar navigation, data table, and native dialog. They are
covered by the repository MIT license and have per-file SHA-256 metadata.

All entries remain `candidate`; none is approved for production use. License
review, build integration, unit tests, accessibility testing, and human visual
review are still pending. There is no renderer or generated-UI build pipeline.
The login fragment is a presentation pattern only and requires CSRF protection
and server-side validation in its host application.

- `catalog.schema.json` defines record structure and approval gates.
- `selection.schema.json` defines a constrained selection format.
- `catalog.json` contains the 10 candidate records; no selection can use them
  until their status is promoted to `approved` after review.
- `components/` contains original escaped Jinja fragments, not third-party assets.

Run `python blackboard/component_catalog/validate_catalog.py` to validate schema
and local source hashes. Validation does not establish legal compliance,
accessibility, or visual quality. Any future renderer must resolve IDs from this
catalog and validate props; it must not execute model-provided code or commands.
