# Profile templates — Phase 3 scaffold

This tree holds the Jinja2 template sources the drift-sync workflow renders
into consumer repos. The skeleton is staged here so downstream tooling
(``scripts/quality/marker_regions.py``, ``reusable-drift-sync.yml``) can
resolve paths even while individual stack templates are still being
authored one repo-proof at a time.

## Layout

```
templates/
  README.md                    # this file
  common/
    ci-fragments/              # shared step snippets referenced by stacks
  stack/
    fullstack-web/             # React/Vite/Vitest + Python/FastAPI (event-link)
    python-only/               # pytest + coverage, no frontend
    react-vite-vitest/         # ui-only: vitest + v8 + eslint + prettier
    go/                        # go test + coverage + golangci-lint
    rust/                      # cargo + tarpaulin + clippy + rustfmt
    swift/                     # xcodebuild + swiftformat + swiftlint + xcov
    cpp-cmake/                 # cmake + ctest + clang-tidy + llvm-cov
    dotnet-wpf/                # dotnet test + coverage + StyleCop
    gradle-java/               # gradle + jacoco + checkstyle + spotbugs
    python-tooling/            # CLI / library packages (this platform's own stack)
```

## Render contract

Each stack directory receives the **entire resolved profile** (the output of
``export_profile.py``) as the Jinja2 context plus the following additional
variables:

- ``repo_slug`` — ``owner/name``
- ``default_branch`` — always ``main`` across the fleet
- ``platform_pin`` — 40-char SHA of the platform commit that rendered the
  templates (pinned to consumer repos via action-pin SHAs on reusable
  workflows)

## Marked regions

Consumer files are rendered into ``BEGIN quality-zero:<region-id>`` /
``END quality-zero:<region-id>`` fences. Only bytes between the fences are
considered owned by the platform; anything above/below is user-owned and
preserved untouched by the drift-sync diff.

See ``scripts/quality/marker_regions.py`` for the parser contract and
``tests/test_marker_regions.py`` for the round-trip specification.
