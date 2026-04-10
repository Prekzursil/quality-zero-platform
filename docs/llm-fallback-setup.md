# LLM Fallback Patch Generation Setup

## What is the LLM Fallback?

The LLM fallback scaffold (design sections 5.2 and A.2.1) provides a mechanism for
generating patches via a language model when deterministic patch generators decline
a finding. This is an opt-in feature that is disabled by default.

## HMAC Key: `QZP_LLM_CACHE_HMAC_KEY`

The LLM fallback uses HMAC-SHA256 signing to authenticate cached patches. This
prevents cache poisoning attacks where a malicious actor injects crafted patches
into the cache.

### Provisioning

```bash
gh secret set QZP_LLM_CACHE_HMAC_KEY \
  --body "$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

### Rotation

1. Generate a new key using the command above.
2. Bump `cache_version` in the LLM fallback configuration.
3. Old cache entries expire via GitHub Actions' 7-day TTL.

## Opt-In Flag

The LLM fallback never runs in CI unless explicitly enabled:

```bash
python -m scripts.quality.rollup_v2 \
  --artifacts-dir artifacts/ \
  --output-dir output/ \
  --repo owner/repo \
  --sha abc123 \
  --enable-llm-patches
```

Without `--enable-llm-patches`, the pipeline skips LLM patch generation entirely.

## Budget Cap

- `--max-llm-patches N` (default: 10) -- maximum number of LLM API calls per run.
- `QZP_LLM_BUDGET_USD` -- read from `.metaswarm/external-tools.yaml` to enforce
  a cost ceiling.

## Preflight Check

If `--enable-llm-patches` is set but `QZP_LLM_CACHE_HMAC_KEY` is not provisioned,
the pipeline raises a fatal error at startup. This fail-fast behavior prevents
unauthenticated cache writes.

## References

- Design doc: `docs/plans/2026-04-09-quality-rollup-v2-design.md` sections 5.2, A.2.1, B.3.12
- Preflight implementation: `scripts/quality/rollup_v2/llm_fallback/preflight.py`
