---
description: Configure a knowledge base freshly created from this template — language, owners, scopes, thresholds, hook, example entity
---

Walk the user through the one-time configuration of this knowledge base. Argument (optional — a single step to jump to, e.g. `owners`, `scopes`, `language`): $ARGUMENTS

Use the **`setup` skill** and follow its steps in order. Everything it does is
configuration (`tools/tasks/schema.yaml`, `tools/context-lint/config.yaml`) — never a
change to a `.py` file.

Confirm each step with the user before writing. If an argument was given, start at that
step, but still say which earlier steps were skipped: the scope roots in step 3 have to
agree across two files, and configuring one of them alone leaves the tools disagreeing.
