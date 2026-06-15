```markdown
# quality-zero-platform Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill introduces the core development patterns and workflows used in the `quality-zero-platform` Python codebase. You'll learn about file naming conventions, import/export styles, commit patterns, and how to keep your feature branches synchronized with the main branch using a standardized workflow. This guide is ideal for contributors aiming for consistency and efficiency in collaborative development.

## Coding Conventions

### File Naming
- Use **snake_case** for all Python files.
  - Example: `data_processor.py`, `user_profile.py`

### Import Style
- Prefer **relative imports** within the package.
  - Example:
    ```python
    from .utils import calculate_score
    from ..models import User
    ```

### Export Style
- Use **named exports** (explicitly define what is exported).
  - Example:
    ```python
    __all__ = ["calculate_score", "User"]
    ```

### Commit Patterns
- Commit types are mixed, with common prefixes like `fix` and `style`.
- Keep commit messages concise (average length: ~68 characters).
  - Example:
    ```
    fix: resolve edge case in score calculation
    style: reformat user_profile.py for PEP8 compliance
    ```

## Workflows

### Merge Main Into Feature Branch
**Trigger:** When a feature/fix branch needs to be synchronized with `main` before further development or merging.  
**Command:** `/merge-main`

1. **Checkout the feature branch**
    ```bash
    git checkout my-feature-branch
    ```
2. **Merge `main` into the feature branch**
    ```bash
    git merge main
    ```
3. **Resolve any conflicts**
    - Edit conflicting files as needed (e.g., in `scripts/quality/*.py`, `tests/*.py`, workflow YAMLs).
    - Use `git status` to identify and resolve all conflicts.
4. **Update affected scripts and tests as needed**
    - Adjust code or tests if upstream changes require it.
    - Example:
      ```python
      # Update imports if file structure changed
      from .new_utils import updated_function
      ```
5. **Commit the merge**
    ```bash
    git add .
    git commit -m "Merge main into my-feature-branch"
    ```

**Files Involved:**
- `scripts/quality/*.py`
- `tests/*.py`
- `.github/workflows/*.yml`
- `profiles/repos/*.yml`
- `templates/repo/.github/workflows/*.yml`

**Frequency:** ~2x/month

## Testing Patterns

- **Framework:** Not explicitly detected; may be custom or standard Python testing tools.
- **File Pattern:** Test files are named with a `.test.ts` suffix (suggesting some TypeScript tests or cross-language testing).
  - Example: `user_profile.test.ts`
- **Best Practice:** Keep tests in the `tests/` directory and name them descriptively.

## Commands

| Command      | Purpose                                                         |
|--------------|-----------------------------------------------------------------|
| /merge-main  | Synchronize your feature/fix branch with the latest `main` code |
```
