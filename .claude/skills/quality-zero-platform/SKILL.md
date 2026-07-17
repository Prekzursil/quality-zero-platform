```markdown
# quality-zero-platform Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill introduces the core development patterns and conventions used in the `quality-zero-platform` Python codebase. It covers file organization, code style, commit practices, and testing approaches, providing practical examples and step-by-step workflows to streamline contributions and maintain code quality.

## Coding Conventions

### File Naming
- Use **snake_case** for all file names.
  - Example: `user_service.py`, `data_loader.py`

### Import Style
- Prefer **relative imports** within the package.
  - Example:
    ```python
    from .utils import calculate_score
    from ..models import User
    ```

### Export Style
- Use **named exports** by explicitly listing public objects in `__all__`.
  - Example:
    ```python
    __all__ = ['UserService', 'calculate_score']
    ```

### Commit Messages
- Use **conventional commit** format.
- Prefix commit messages with the type, such as `ci`.
- Example:
  ```
  ci: update deployment workflow for staging environment
  ```

## Workflows

### Commit Code Changes
**Trigger:** When making any code changes that need to be committed.
**Command:** `/commit`

1. Stage your changes:
   ```
   git add .
   ```
2. Write a commit message using the conventional format:
   ```
   git commit -m "ci: short description of the change"
   ```
3. Push your changes:
   ```
   git push
   ```

### Add a New Module
**Trigger:** When introducing a new feature or logical component.
**Command:** `/add-module`

1. Create a new Python file using snake_case:
   ```
   touch new_feature.py
   ```
2. Use relative imports for dependencies within the package.
3. Define public exports in `__all__`.
4. Commit your changes using the conventional commit format.

### Refactor Existing Code
**Trigger:** When improving or restructuring code without changing its behavior.
**Command:** `/refactor`

1. Update code using snake_case for files and relative imports.
2. Adjust `__all__` as needed for exports.
3. Commit with a message like:
   ```
   git commit -m "ci: refactor user service for readability"
   ```

## Testing Patterns

- **Framework:** Not explicitly detected; ensure to use a consistent Python testing framework (e.g., `pytest` or `unittest`).
- **Test File Naming:** While the repository contains `*.test.ts` files (TypeScript), for Python, use `test_*.py`.
  - Example: `test_user_service.py`
- **Test Example:**
  ```python
  import unittest
  from .user_service import UserService

  class TestUserService(unittest.TestCase):
      def test_create_user(self):
          service = UserService()
          self.assertTrue(service.create_user("alice"))
  ```

## Commands
| Command      | Purpose                                      |
|--------------|----------------------------------------------|
| /commit      | Commit code changes using conventional format|
| /add-module  | Add a new Python module following conventions|
| /refactor    | Refactor code while maintaining conventions  |
```