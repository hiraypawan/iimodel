# Original Flask/Jinja component candidates

These ten small HTML/Jinja fragments were authored for this repository and are
covered by the repository's MIT license. They are candidates, not approved
production UI: there is no renderer, visual review, automated accessibility audit,
or end-to-end build pipeline yet. The login fragment explicitly requires the
host application to provide CSRF protection and server-side validation. All
variable output uses Jinja's explicit `e` filter; URL schemes and data semantics
still require host-side validation.

The catalog records per-file SHA-256 values and source paths. To promote a
candidate, complete the catalog's license, build, unit-test, accessibility, and
visual-review fields with human review.
