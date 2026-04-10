
## Provider Summary

| Provider | Total | High | Medium | Low |
|----------|------:|-----:|-------:|----:|
| Codacy | 8 | 0 | 8 | 0 |
| DeepScan | 8 | 0 | 0 | 0 |
| DeepSource | 8 | 0 | 0 | 8 |
| QLTY | 9 | 0 | 0 | 0 |
| SonarCloud | 9 | 9 | 0 | 0 |

### `src/api/auth.py` (9 findings)

#### 🔴 line 10 · `unused-import` · **critical** · 1 provider

**Message:** Finding 0: unused-import detected in src/api/auth.py

**Providers:** [QLTY](https://rules.example.com/unused-import)

**Fix hint:** Fix hint for finding 0

```diff
--- a/src/api/auth.py
+++ b/src/api/auth.py
@@ -10,1 +10,1 @@
-old line 0
+new line 0
```

#### 🔴 line 15 · `missing-docstring` · **critical** · 1 provider

**Message:** Finding 35: missing-docstring detected in src/api/auth.py

**Providers:** QLTY

```diff
--- a/src/api/auth.py
+++ b/src/api/auth.py
@@ -15,1 +15,1 @@
-old line 35
+new line 35
```

#### 🔴 line 25 · `dead-code` · **critical** · 1 provider

**Message:** Finding 5: dead-code detected in src/api/auth.py

**Providers:** QLTY

```diff
--- a/src/api/auth.py
+++ b/src/api/auth.py
@@ -25,1 +25,1 @@
-old line 5
+new line 5
```

#### 🔴 line 30 · `unused-import` · **critical** · 1 provider

**Message:** Finding 40: unused-import detected in src/api/auth.py

**Providers:** QLTY

**Fix hint:** Fix hint for finding 40

_No automated patch available_

#### 🔴 line 40 · `hardcoded-secret` · **critical** · 1 provider

**Message:** Finding 10: hardcoded-secret detected in src/api/auth.py

**Providers:** QLTY

_No automated patch available_

#### 🔴 line 55 · `line-too-long` · **critical** · 1 provider

**Message:** Finding 15: line-too-long detected in src/api/auth.py

**Providers:** [QLTY](https://rules.example.com/line-too-long)

```diff
--- a/src/api/auth.py
+++ b/src/api/auth.py
@@ -55,1 +55,1 @@
-old line 15
+new line 15
```

#### 🔴 line 70 · `too-complex` · **critical** · 1 provider

**Message:** Finding 20: too-complex detected in src/api/auth.py

**Providers:** QLTY

**Fix hint:** Fix hint for finding 20

```diff
--- a/src/api/auth.py
+++ b/src/api/auth.py
@@ -70,1 +70,1 @@
-old line 20
+new line 20
```

#### 🔴 line 85 · `broad-except` · **critical** · 1 provider

**Message:** Finding 25: broad-except detected in src/api/auth.py

**Providers:** QLTY

_No automated patch available_

#### 🔴 line 100 · `unused-variable` · **critical** · 1 provider

**Message:** Finding 30: unused-variable detected in src/api/auth.py

**Providers:** [QLTY](https://rules.example.com/unused-variable)

```diff
--- a/src/api/auth.py
+++ b/src/api/auth.py
@@ -100,1 +100,1 @@
-old line 30
+new line 30
```

### `src/core/models.py` (9 findings)

#### 🔴 line 13 · `broad-except` · **high** · 1 provider

**Message:** Finding 1: broad-except detected in src/core/models.py

**Providers:** SonarCloud

_No automated patch available_

#### 🔴 line 18 · `too-complex` · **high** · 1 provider

**Message:** Finding 36: too-complex detected in src/core/models.py

**Providers:** [SonarCloud](https://rules.example.com/too-complex)

**Fix hint:** Fix hint for finding 36

```diff
--- a/src/core/models.py
+++ b/src/core/models.py
@@ -18,1 +18,1 @@
-old line 36
+new line 36
```

#### 🔴 line 28 · `unused-variable` · **high** · 1 provider

**Message:** Finding 6: unused-variable detected in src/core/models.py

**Providers:** [SonarCloud](https://rules.example.com/unused-variable)

```diff
--- a/src/core/models.py
+++ b/src/core/models.py
@@ -28,1 +28,1 @@
-old line 6
+new line 6
```

#### 🔴 line 33 · `broad-except` · **high** · 1 provider

**Message:** Finding 41: broad-except detected in src/core/models.py

**Providers:** SonarCloud

```diff
--- a/src/core/models.py
+++ b/src/core/models.py
@@ -33,1 +33,1 @@
-old line 41
+new line 41
```

#### 🔴 line 43 · `missing-docstring` · **high** · 1 provider

**Message:** Finding 11: missing-docstring detected in src/core/models.py

**Providers:** SonarCloud

```diff
--- a/src/core/models.py
+++ b/src/core/models.py
@@ -43,1 +43,1 @@
-old line 11
+new line 11
```

#### 🔴 line 58 · `unused-import` · **high** · 1 provider

**Message:** Finding 16: unused-import detected in src/core/models.py

**Providers:** SonarCloud

**Fix hint:** Fix hint for finding 16

_No automated patch available_

#### 🔴 line 73 · `dead-code` · **high** · 1 provider

**Message:** Finding 21: dead-code detected in src/core/models.py

**Providers:** [SonarCloud](https://rules.example.com/dead-code)

```diff
--- a/src/core/models.py
+++ b/src/core/models.py
@@ -73,1 +73,1 @@
-old line 21
+new line 21
```

#### 🔴 line 88 · `hardcoded-secret` · **high** · 1 provider

**Message:** Finding 26: hardcoded-secret detected in src/core/models.py

**Providers:** SonarCloud

```diff
--- a/src/core/models.py
+++ b/src/core/models.py
@@ -88,1 +88,1 @@
-old line 26
+new line 26
```

#### 🔴 line 103 · `line-too-long` · **high** · 1 provider

**Message:** Finding 31: line-too-long detected in src/core/models.py

**Providers:** SonarCloud

_No automated patch available_

### `config/settings.py` (8 findings)

#### ⚪ line 12 · `hardcoded-secret` · **info** · 1 provider

**Message:** Finding 34: hardcoded-secret detected in config/settings.py

**Providers:** DeepScan

_No automated patch available_

#### ⚪ line 22 · `too-complex` · **info** · 1 provider

**Message:** Finding 4: too-complex detected in config/settings.py

**Providers:** DeepScan

**Fix hint:** Fix hint for finding 4

_No automated patch available_

#### ⚪ line 27 · `line-too-long` · **info** · 1 provider

**Message:** Finding 39: line-too-long detected in config/settings.py

**Providers:** [DeepScan](https://rules.example.com/line-too-long)

```diff
--- a/config/settings.py
+++ b/config/settings.py
@@ -27,1 +27,1 @@
-old line 39
+new line 39
```

#### ⚪ line 37 · `broad-except` · **info** · 1 provider

**Message:** Finding 9: broad-except detected in config/settings.py

**Providers:** [DeepScan](https://rules.example.com/broad-except)

```diff
--- a/config/settings.py
+++ b/config/settings.py
@@ -37,1 +37,1 @@
-old line 9
+new line 9
```

#### ⚪ line 52 · `unused-variable` · **info** · 1 provider

**Message:** Finding 14: unused-variable detected in config/settings.py

**Providers:** DeepScan

```diff
--- a/config/settings.py
+++ b/config/settings.py
@@ -52,1 +52,1 @@
-old line 14
+new line 14
```

#### ⚪ line 67 · `missing-docstring` · **info** · 1 provider

**Message:** Finding 19: missing-docstring detected in config/settings.py

**Providers:** DeepScan

_No automated patch available_

#### ⚪ line 82 · `unused-import` · **info** · 1 provider

**Message:** Finding 24: unused-import detected in config/settings.py

**Providers:** [DeepScan](https://rules.example.com/unused-import)

**Fix hint:** Fix hint for finding 24

```diff
--- a/config/settings.py
+++ b/config/settings.py
@@ -82,1 +82,1 @@
-old line 24
+new line 24
```

#### ⚪ line 97 · `dead-code` · **info** · 1 provider

**Message:** Finding 29: dead-code detected in config/settings.py

**Providers:** DeepScan

```diff
--- a/config/settings.py
+++ b/config/settings.py
@@ -97,1 +97,1 @@
-old line 29
+new line 29
```

### `src/utils/helpers.py` (8 findings)

#### 🟡 line 16 · `hardcoded-secret` · **medium** · 1 provider

**Message:** Finding 2: hardcoded-secret detected in src/utils/helpers.py

**Providers:** Codacy

```diff
--- a/src/utils/helpers.py
+++ b/src/utils/helpers.py
@@ -16,1 +16,1 @@
-old line 2
+new line 2
```

#### 🟡 line 21 · `dead-code` · **medium** · 1 provider

**Message:** Finding 37: dead-code detected in src/utils/helpers.py

**Providers:** Codacy

_No automated patch available_

#### 🟡 line 31 · `line-too-long` · **medium** · 1 provider

**Message:** Finding 7: line-too-long detected in src/utils/helpers.py

**Providers:** Codacy

_No automated patch available_

#### 🟡 line 46 · `too-complex` · **medium** · 1 provider

**Message:** Finding 12: too-complex detected in src/utils/helpers.py

**Providers:** [Codacy](https://rules.example.com/too-complex)

**Fix hint:** Fix hint for finding 12

```diff
--- a/src/utils/helpers.py
+++ b/src/utils/helpers.py
@@ -46,1 +46,1 @@
-old line 12
+new line 12
```

#### 🟡 line 61 · `broad-except` · **medium** · 1 provider

**Message:** Finding 17: broad-except detected in src/utils/helpers.py

**Providers:** Codacy

```diff
--- a/src/utils/helpers.py
+++ b/src/utils/helpers.py
@@ -61,1 +61,1 @@
-old line 17
+new line 17
```

#### 🟡 line 76 · `unused-variable` · **medium** · 1 provider

**Message:** Finding 22: unused-variable detected in src/utils/helpers.py

**Providers:** Codacy

_No automated patch available_

#### 🟡 line 91 · `missing-docstring` · **medium** · 1 provider

**Message:** Finding 27: missing-docstring detected in src/utils/helpers.py

**Providers:** [Codacy](https://rules.example.com/missing-docstring)

```diff
--- a/src/utils/helpers.py
+++ b/src/utils/helpers.py
@@ -91,1 +91,1 @@
-old line 27
+new line 27
```

#### 🟡 line 106 · `unused-import` · **medium** · 1 provider

**Message:** Finding 32: unused-import detected in src/utils/helpers.py

**Providers:** Codacy

**Fix hint:** Fix hint for finding 32

```diff
--- a/src/utils/helpers.py
+++ b/src/utils/helpers.py
@@ -106,1 +106,1 @@
-old line 32
+new line 32
```

### `tests/test_auth.py` (8 findings)

#### ⚪ line 19 · `missing-docstring` · **low** · 1 provider

**Message:** Finding 3: missing-docstring detected in tests/test_auth.py

**Providers:** [DeepSource](https://rules.example.com/missing-docstring)

```diff
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -19,1 +19,1 @@
-old line 3
+new line 3
```

#### ⚪ line 24 · `unused-variable` · **low** · 1 provider

**Message:** Finding 38: unused-variable detected in tests/test_auth.py

**Providers:** DeepSource

```diff
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -24,1 +24,1 @@
-old line 38
+new line 38
```

#### ⚪ line 34 · `unused-import` · **low** · 1 provider

**Message:** Finding 8: unused-import detected in tests/test_auth.py

**Providers:** DeepSource

**Fix hint:** Fix hint for finding 8

```diff
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -34,1 +34,1 @@
-old line 8
+new line 8
```

#### ⚪ line 49 · `dead-code` · **low** · 1 provider

**Message:** Finding 13: dead-code detected in tests/test_auth.py

**Providers:** DeepSource

_No automated patch available_

#### ⚪ line 64 · `hardcoded-secret` · **low** · 1 provider

**Message:** Finding 18: hardcoded-secret detected in tests/test_auth.py

**Providers:** [DeepSource](https://rules.example.com/hardcoded-secret)

```diff
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -64,1 +64,1 @@
-old line 18
+new line 18
```

#### ⚪ line 79 · `line-too-long` · **low** · 1 provider

**Message:** Finding 23: line-too-long detected in tests/test_auth.py

**Providers:** DeepSource

```diff
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -79,1 +79,1 @@
-old line 23
+new line 23
```

#### ⚪ line 94 · `too-complex` · **low** · 1 provider

**Message:** Finding 28: too-complex detected in tests/test_auth.py

**Providers:** DeepSource

**Fix hint:** Fix hint for finding 28

_No automated patch available_

#### ⚪ line 109 · `broad-except` · **low** · 1 provider

**Message:** Finding 33: broad-except detected in tests/test_auth.py

**Providers:** [DeepSource](https://rules.example.com/broad-except)

```diff
--- a/tests/test_auth.py
+++ b/tests/test_auth.py
@@ -109,1 +109,1 @@
-old line 33
+new line 33
```

<details><summary>View by provider</summary>

### Codacy (8 findings)

- 🟡 `src/utils/helpers.py` line 16 · `hardcoded-secret` · **medium**
- 🟡 `src/utils/helpers.py` line 21 · `dead-code` · **medium**
- 🟡 `src/utils/helpers.py` line 31 · `line-too-long` · **medium**
- 🟡 `src/utils/helpers.py` line 46 · `too-complex` · **medium**
- 🟡 `src/utils/helpers.py` line 61 · `broad-except` · **medium**
- 🟡 `src/utils/helpers.py` line 76 · `unused-variable` · **medium**
- 🟡 `src/utils/helpers.py` line 91 · `missing-docstring` · **medium**
- 🟡 `src/utils/helpers.py` line 106 · `unused-import` · **medium**

### DeepScan (8 findings)

- ⚪ `config/settings.py` line 12 · `hardcoded-secret` · **info**
- ⚪ `config/settings.py` line 22 · `too-complex` · **info**
- ⚪ `config/settings.py` line 27 · `line-too-long` · **info**
- ⚪ `config/settings.py` line 37 · `broad-except` · **info**
- ⚪ `config/settings.py` line 52 · `unused-variable` · **info**
- ⚪ `config/settings.py` line 67 · `missing-docstring` · **info**
- ⚪ `config/settings.py` line 82 · `unused-import` · **info**
- ⚪ `config/settings.py` line 97 · `dead-code` · **info**

### DeepSource (8 findings)

- ⚪ `tests/test_auth.py` line 19 · `missing-docstring` · **low**
- ⚪ `tests/test_auth.py` line 24 · `unused-variable` · **low**
- ⚪ `tests/test_auth.py` line 34 · `unused-import` · **low**
- ⚪ `tests/test_auth.py` line 49 · `dead-code` · **low**
- ⚪ `tests/test_auth.py` line 64 · `hardcoded-secret` · **low**
- ⚪ `tests/test_auth.py` line 79 · `line-too-long` · **low**
- ⚪ `tests/test_auth.py` line 94 · `too-complex` · **low**
- ⚪ `tests/test_auth.py` line 109 · `broad-except` · **low**

### QLTY (9 findings)

- 🔴 `src/api/auth.py` line 10 · `unused-import` · **critical**
- 🔴 `src/api/auth.py` line 15 · `missing-docstring` · **critical**
- 🔴 `src/api/auth.py` line 25 · `dead-code` · **critical**
- 🔴 `src/api/auth.py` line 30 · `unused-import` · **critical**
- 🔴 `src/api/auth.py` line 40 · `hardcoded-secret` · **critical**
- 🔴 `src/api/auth.py` line 55 · `line-too-long` · **critical**
- 🔴 `src/api/auth.py` line 70 · `too-complex` · **critical**
- 🔴 `src/api/auth.py` line 85 · `broad-except` · **critical**
- 🔴 `src/api/auth.py` line 100 · `unused-variable` · **critical**

### SonarCloud (9 findings)

- 🔴 `src/core/models.py` line 13 · `broad-except` · **high**
- 🔴 `src/core/models.py` line 18 · `too-complex` · **high**
- 🔴 `src/core/models.py` line 28 · `unused-variable` · **high**
- 🔴 `src/core/models.py` line 33 · `broad-except` · **high**
- 🔴 `src/core/models.py` line 43 · `missing-docstring` · **high**
- 🔴 `src/core/models.py` line 58 · `unused-import` · **high**
- 🔴 `src/core/models.py` line 73 · `dead-code` · **high**
- 🔴 `src/core/models.py` line 88 · `hardcoded-secret` · **high**
- 🔴 `src/core/models.py` line 103 · `line-too-long` · **high**

</details>

<details><summary>View by severity</summary>

### 🔴 Critical (9 findings)

- `src/api/auth.py` line 10 · `unused-import` · 1 provider
- `src/api/auth.py` line 15 · `missing-docstring` · 1 provider
- `src/api/auth.py` line 25 · `dead-code` · 1 provider
- `src/api/auth.py` line 30 · `unused-import` · 1 provider
- `src/api/auth.py` line 40 · `hardcoded-secret` · 1 provider
- `src/api/auth.py` line 55 · `line-too-long` · 1 provider
- `src/api/auth.py` line 70 · `too-complex` · 1 provider
- `src/api/auth.py` line 85 · `broad-except` · 1 provider
- `src/api/auth.py` line 100 · `unused-variable` · 1 provider

### 🔴 High (9 findings)

- `src/core/models.py` line 13 · `broad-except` · 1 provider
- `src/core/models.py` line 18 · `too-complex` · 1 provider
- `src/core/models.py` line 28 · `unused-variable` · 1 provider
- `src/core/models.py` line 33 · `broad-except` · 1 provider
- `src/core/models.py` line 43 · `missing-docstring` · 1 provider
- `src/core/models.py` line 58 · `unused-import` · 1 provider
- `src/core/models.py` line 73 · `dead-code` · 1 provider
- `src/core/models.py` line 88 · `hardcoded-secret` · 1 provider
- `src/core/models.py` line 103 · `line-too-long` · 1 provider

### 🟡 Medium (8 findings)

- `src/utils/helpers.py` line 16 · `hardcoded-secret` · 1 provider
- `src/utils/helpers.py` line 21 · `dead-code` · 1 provider
- `src/utils/helpers.py` line 31 · `line-too-long` · 1 provider
- `src/utils/helpers.py` line 46 · `too-complex` · 1 provider
- `src/utils/helpers.py` line 61 · `broad-except` · 1 provider
- `src/utils/helpers.py` line 76 · `unused-variable` · 1 provider
- `src/utils/helpers.py` line 91 · `missing-docstring` · 1 provider
- `src/utils/helpers.py` line 106 · `unused-import` · 1 provider

### ⚪ Low (8 findings)

- `tests/test_auth.py` line 19 · `missing-docstring` · 1 provider
- `tests/test_auth.py` line 24 · `unused-variable` · 1 provider
- `tests/test_auth.py` line 34 · `unused-import` · 1 provider
- `tests/test_auth.py` line 49 · `dead-code` · 1 provider
- `tests/test_auth.py` line 64 · `hardcoded-secret` · 1 provider
- `tests/test_auth.py` line 79 · `line-too-long` · 1 provider
- `tests/test_auth.py` line 94 · `too-complex` · 1 provider
- `tests/test_auth.py` line 109 · `broad-except` · 1 provider

### ⚪ Info (8 findings)

- `config/settings.py` line 12 · `hardcoded-secret` · 1 provider
- `config/settings.py` line 22 · `too-complex` · 1 provider
- `config/settings.py` line 27 · `line-too-long` · 1 provider
- `config/settings.py` line 37 · `broad-except` · 1 provider
- `config/settings.py` line 52 · `unused-variable` · 1 provider
- `config/settings.py` line 67 · `missing-docstring` · 1 provider
- `config/settings.py` line 82 · `unused-import` · 1 provider
- `config/settings.py` line 97 · `dead-code` · 1 provider

</details>

<details><summary>Autofixable only</summary>

**28 autofixable findings:**

- ⚪ `config/settings.py` line 27 · `line-too-long` · **deterministic**
- ⚪ `config/settings.py` line 37 · `broad-except` · **deterministic**
- ⚪ `config/settings.py` line 52 · `unused-variable` · **llm**
- ⚪ `config/settings.py` line 82 · `unused-import` · **deterministic**
- ⚪ `config/settings.py` line 97 · `dead-code` · **llm**
- 🔴 `src/api/auth.py` line 10 · `unused-import` · **deterministic**
- 🔴 `src/api/auth.py` line 15 · `missing-docstring` · **llm**
- 🔴 `src/api/auth.py` line 25 · `dead-code` · **llm**
- 🔴 `src/api/auth.py` line 55 · `line-too-long` · **deterministic**
- 🔴 `src/api/auth.py` line 70 · `too-complex` · **llm**
- 🔴 `src/api/auth.py` line 100 · `unused-variable` · **deterministic**
- 🔴 `src/core/models.py` line 18 · `too-complex` · **deterministic**
- 🔴 `src/core/models.py` line 28 · `unused-variable` · **deterministic**
- 🔴 `src/core/models.py` line 33 · `broad-except` · **llm**
- 🔴 `src/core/models.py` line 43 · `missing-docstring` · **llm**
- 🔴 `src/core/models.py` line 73 · `dead-code` · **deterministic**
- 🔴 `src/core/models.py` line 88 · `hardcoded-secret` · **llm**
- 🟡 `src/utils/helpers.py` line 16 · `hardcoded-secret` · **llm**
- 🟡 `src/utils/helpers.py` line 46 · `too-complex` · **deterministic**
- 🟡 `src/utils/helpers.py` line 61 · `broad-except` · **llm**
- 🟡 `src/utils/helpers.py` line 91 · `missing-docstring` · **deterministic**
- 🟡 `src/utils/helpers.py` line 106 · `unused-import` · **llm**
- ⚪ `tests/test_auth.py` line 19 · `missing-docstring` · **deterministic**
- ⚪ `tests/test_auth.py` line 24 · `unused-variable` · **llm**
- ⚪ `tests/test_auth.py` line 34 · `unused-import` · **llm**
- ⚪ `tests/test_auth.py` line 64 · `hardcoded-secret` · **deterministic**
- ⚪ `tests/test_auth.py` line 79 · `line-too-long` · **llm**
- ⚪ `tests/test_auth.py` line 109 · `broad-except` · **deterministic**

</details>

ℹ️ [How to read this report](docs/quality-rollup-guide.md) · [Schema v1](docs/schemas/qzp-finding-v1.md) · [Report a format issue](https://github.com/user/quality-zero-platform/issues/new?labels=rollup-format)
