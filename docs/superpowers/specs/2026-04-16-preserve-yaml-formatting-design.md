# Preserve YAML Order and Format in Release PRs

**Linear:** ST-3803
**Date:** 2026-04-16

## Context

When helm-image-updater creates release PRs that modify `tag.yaml` and `values.yaml` files, the resulting diffs show unwanted formatting changes alongside the actual value updates. This is caused by PyYAML's `dump()` which re-serializes YAML from scratch, destroying:

- **Key ordering** — keys get alphabetically sorted
- **Blank lines** — separators between logical sections are stripped
- **Quote styles** — `"10m"` becomes `10m`, `"https://..."` becomes unquoted
- **Comments** — any `#` comments are lost

Example: [kbc-stacks commit f3efe45](https://github.com/keboola/kbc-stacks/commit/f3efe458f1e798c49051b5b7078ef374c1566804) — a simple tag update + override removal produced a massive diff reordering every key in `values.yaml`.

## Approach

Replace PyYAML with **ruamel.yaml** (round-trip mode) in all code paths that write YAML back to disk. ruamel.yaml preserves comments, key order, blank lines, and quote styles by default.

Read-only YAML operations (`read_yaml()`, `read_shared_values_yaml()`) remain on PyYAML since they return plain dicts for consumption and never write back.

## Changes

### 1. `plan_builder.py` — `_calculate_all_changes()` (lines 193-217)

**Before:** Reads file content as string, parses with `yaml.safe_load()`, applies changes to plain dict, re-serializes with `yaml.dump()`.

**After:** Parse with `ruamel.yaml` round-trip loader, apply changes to the `CommentedMap` (which supports the same dict operations), serialize with `ruamel.yaml` dump to a `StringIO` to produce `new_content`.

```python
from ruamel.yaml import YAML
from io import StringIO

ryaml = YAML()
ryaml.preserve_quotes = True

# In _calculate_all_changes():
current_data = ryaml.load(current_content)
# ... calculate_tag_changes() and _apply_changes_to_data() work unchanged
# ... (CommentedMap is a dict subclass)
new_data = _apply_changes_to_data(current_data, changes)
stream = StringIO()
ryaml.dump(new_data, stream)
new_content = stream.getvalue()
```

### 2. `plan_builder.py` — `_check_and_remove_override()` (lines 264-289)

**Before:** Parses `values.yaml` with `yaml.safe_load()`, deletes the override key, re-serializes with `yaml.dump()`.

**After:** Same pattern — parse with ruamel.yaml, delete the key, serialize preserving formatting.

```python
# In _check_and_remove_override():
values_data = ryaml.load(values_content)
# ... existing dict operations (get, del, isinstance) work unchanged
stream = StringIO()
ryaml.dump(new_data, stream)
new_content = stream.getvalue()
```

### 3. `io_layer.py` — `write_file_changes()` (lines 112-122)

**Before:** Re-parses `new_content` with `yaml.safe_load()` and re-dumps via `write_yaml()` — causing a second round of formatting destruction.

**After:** Write `new_content` directly as text for all files (remove the YAML-specific branch). The `new_content` from `plan_builder.py` is already correctly formatted by ruamel.yaml.

```python
def write_file_changes(self, file_changes) -> bool:
    if self.dry_run:
        for file_change in file_changes:
            print(f"[DRY RUN] Would write to {file_change.file_path}")
        return False

    for file_change in file_changes:
        file_path = Path(file_change.file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open('w') as f:
            f.write(file_change.new_content)

    return True
```

### 4. `io_layer.py` — `write_yaml()` (line 94)

Add `sort_keys=False` and `default_flow_style=False` as a safety net for any remaining callers:

```python
yaml.dump(data, f, default_flow_style=False, sort_keys=False)
```

### 5. `requirements.txt`

Add `ruamel.yaml>=0.18.0`. (`setup.py` reads from `requirements.txt` so no separate change needed.)

## What stays unchanged

- `calculate_tag_changes()` — pure function operating on dict interface, works with `CommentedMap`
- `_apply_changes_to_data()` — uses `copy.deepcopy()` and dict operations, works with `CommentedMap`
- `read_yaml()`, `read_shared_values_yaml()` — read-only, keep PyYAML
- All model classes (`FileChange`, `UpdatePlan`, etc.) — unchanged
- `plan_executor.py` — unchanged (calls `write_file_changes` which now just writes text)

## Testing

- Existing 74 unit tests must continue to pass
- Test fixtures in `test_plan_builder.py` already create tag.yaml files with specific formatting (using `f.write()` with literal YAML) — these will verify round-trip preservation
- Test fixtures in `test_cli_functional.py` use `yaml.dump()` to create files — these test that the tool handles PyYAML-formatted input correctly too
- Add new tests verifying:
  - Key order is preserved after tag update
  - Blank lines are preserved after tag update
  - Quoted values remain quoted after tag update
  - Override removal preserves formatting of remaining keys in values.yaml

## Verification

1. Run `pytest -sv tests/` — all existing tests pass
2. Run the tool with `DRY_RUN=true` against a real kbc-stacks checkout to verify formatting preservation
3. Trigger E2E tests via `gh workflow run test-suite.yaml --repo keboola/helm-image-updater-testing --field helm-image-updater-branch=<branch>`
