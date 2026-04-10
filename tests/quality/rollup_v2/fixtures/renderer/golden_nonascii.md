
## Provider Summary

| Provider | Total | High | Medium | Low |
|----------|------:|-----:|-------:|----:|
| Codacy | 1 | 0 | 1 | 0 |
| DeepScan | 1 | 0 | 0 | 0 |
| DeepSource | 1 | 0 | 0 | 1 |
| QLTY | 1 | 0 | 0 | 0 |
| SonarCloud | 1 | 1 | 0 | 0 |

### `src/café.py` (2 findings)

#### 🔴 line 10 · `unused-import` · **critical** · 1 provider

**Message:** 日本語: finding 0

**Providers:** [QLTY](https://rules.example.com/unused-import)

**Fix hint:** Fix hint for finding 0

```diff
--- a/src/café.py
+++ b/src/café.py
@@ -10,1 +10,1 @@
-old line 0
+new line 0
```

#### ⚪ line 19 · `missing-docstring` · **low** · 1 provider

**Message:** Finding 3: missing-docstring detected in src/café.py

**Providers:** [DeepSource](https://rules.example.com/missing-docstring)

```diff
--- a/src/café.py
+++ b/src/café.py
@@ -19,1 +19,1 @@
-old line 3
+new line 3
```

### `src/日本語/app.py` (2 findings)

#### 🔴 line 13 · `broad-except` · **high** · 1 provider

**Message:** Finding 1: broad-except detected in src/日本語/app.py

**Providers:** SonarCloud

_No automated patch available_

#### ⚪ line 22 · `too-complex` · **info** · 1 provider

**Message:** 日本語: finding 4

**Providers:** DeepScan

**Fix hint:** Fix hint for finding 4

_No automated patch available_

### `src/api/auth.py` (1 finding)

#### 🟡 line 16 · `hardcoded-secret` · **medium** · 1 provider

**Message:** 日本語: finding 2

**Providers:** Codacy

```diff
--- a/src/api/auth.py
+++ b/src/api/auth.py
@@ -16,1 +16,1 @@
-old line 2
+new line 2
```

<details><summary>View by provider</summary>

### Codacy (1 finding)

- 🟡 `src/api/auth.py` line 16 · `hardcoded-secret` · **medium**

### DeepScan (1 finding)

- ⚪ `src/日本語/app.py` line 22 · `too-complex` · **info**

### DeepSource (1 finding)

- ⚪ `src/café.py` line 19 · `missing-docstring` · **low**

### QLTY (1 finding)

- 🔴 `src/café.py` line 10 · `unused-import` · **critical**

### SonarCloud (1 finding)

- 🔴 `src/日本語/app.py` line 13 · `broad-except` · **high**

</details>

<details><summary>View by severity</summary>

### 🔴 Critical (1 finding)

- `src/café.py` line 10 · `unused-import` · 1 provider

### 🔴 High (1 finding)

- `src/日本語/app.py` line 13 · `broad-except` · 1 provider

### 🟡 Medium (1 finding)

- `src/api/auth.py` line 16 · `hardcoded-secret` · 1 provider

### ⚪ Low (1 finding)

- `src/café.py` line 19 · `missing-docstring` · 1 provider

### ⚪ Info (1 finding)

- `src/日本語/app.py` line 22 · `too-complex` · 1 provider

</details>

<details><summary>Autofixable only</summary>

**3 autofixable findings:**

- 🟡 `src/api/auth.py` line 16 · `hardcoded-secret` · **llm**
- 🔴 `src/café.py` line 10 · `unused-import` · **deterministic**
- ⚪ `src/café.py` line 19 · `missing-docstring` · **deterministic**

</details>

ℹ️ [How to read this report](docs/quality-rollup-guide.md) · [Schema v1](docs/schemas/qzp-finding-v1.md) · [Report a format issue](https://github.com/user/quality-zero-platform/issues/new?labels=rollup-format)
