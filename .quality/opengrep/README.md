# Curated SAST ruleset (Gate 4)

Pinned tool: **opengrep 1.23.0** (CI) — locally interchangeable with **semgrep CE 1.166.0**
(opengrep is a fork of semgrep and consumes the same rule syntax).

## Why an in-repo ruleset instead of `--config auto`

`--config auto` / `p/*` registry packs are fetched from the network at scan time and change
underneath you, which makes the gate **non-deterministic**. On this repo `--config auto` also
produced registry false-positives (e.g. `python.lang.compatibility.python36.*` on a codebase
that targets Python 3.11+). The lean model requires a fixed, reviewable ruleset committed to
the repo, so the gate produces the same result every run, offline, with no registry login.

## Contents

A **curated subset** distilled from the relevant upstream packs (`p/python`, `p/javascript`,
`p/r2c-security-audit`) — the high-signal security rules that apply to this Python + JS/TS
agent-tooling codebase:

- `python-security.yaml` — Python injection (`eval`/`exec`/`os.system`/`shell=True`),
  unsafe-deserialization (`pickle`, unsafe `yaml.load`), weak-crypto (`md5`/`sha1`),
  disabled-TLS/JWT-verification patterns.
- `javascript-security.yaml` — JS/TS XSS / `eval` / `Function` constructor / unsafe DOM sink
  (`innerHTML`, `document.write`) / `child_process.exec` / insecure-randomness patterns.
- `general-security.yaml` — language-agnostic patterns (private key committed, AWS key id).

Upstream registry rules are Apache-2.0 / LGPL-2.1 licensed; rule logic is reproduced / adapted
here. To refresh against upstream, diff the registry packs and port new high-signal rules in
(one-in-one-out review).

## Running the gate

```bash
# CI (opengrep on Linux):
opengrep scan --config .quality/opengrep --error \
  --exclude node_modules --exclude .venv --exclude vendor \
  --exclude dist --exclude out --exclude build .

# Local (semgrep CE, rule-compatible):
semgrep scan --config .quality/opengrep --error --metrics off \
  --exclude node_modules --exclude .venv --exclude vendor \
  --exclude dist --exclude out --exclude build .
```

Gate passes on **0 findings** (clean-zero lock; no baseline file). Genuine false-positives are
suppressed inline with a greppable `# nosemgrep: <rule-id> -- <reason>` comment placed on the
**first line of the matched construct** (for a multi-line call, the line with the call head).
