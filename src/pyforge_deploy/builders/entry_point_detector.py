"""Auto-detection of project entry points for zero-configuration usability.

Discovers executable entry points in the project by:
1. Checking pyproject.toml [project.scripts]
2. Analyzing source for __main__ blocks
3. Detecting console_scripts in setup.py/setup.cfg
4. Finding CLI modules by common naming patterns
"""

import ast
import os
from pathlib import Path
from typing import Any, cast


def find_project_sources(project_path: str) -> list[Path]:
    """Discover all Python source directories in the project.

    Args:
        project_path: Root directory of the project.

    Returns:
        List of Path objects pointing to source directories (src/, ., etc.).
    """
    sources: list[Path] = []
    base = Path(project_path)

    # Check for src/ layout
    src_dir = base / "src"
    if src_dir.exists() and src_dir.is_dir():
        sources.append(src_dir)

    # Check for flat layout (packages in root)
    if (base / "pyproject.toml").exists():
        sources.append(base)

    return sources or [base]


def extract_entry_points_from_pyproject(
    project_path: str,
) -> dict[str, str]:
    """Extract console entry points from pyproject.toml.

    Args:
        project_path: Root directory of the project.

    Returns:
        Dictionary of {script_name: module_path}.
    """
    import toml

    entries: dict[str, str] = {}
    pyproject_file = Path(project_path) / "pyproject.toml"

    if not pyproject_file.exists():
        return entries

    try:
        data_raw: Any = toml.load(str(pyproject_file))
        if not isinstance(data_raw, dict):
            return entries

        data = cast(dict[str, Any], data_raw)
        project_raw = data.get("project", {})
        if not isinstance(project_raw, dict):
            return entries

        project_data = cast(dict[str, Any], project_raw)
        scripts_raw = project_data.get("scripts", {})
        if not isinstance(scripts_raw, dict):
            return entries

        # Extract only string key-value pairs using dict comprehension with type guards
        scripts_items: dict[str, str] = {
            k: v
            for k, v in scripts_raw.items()  # pyright: ignore[reportUnknownVariableType]
            if isinstance(k, str) and isinstance(v, str)
        }
        entries.update(scripts_items)
    except Exception:  # nosec B110
        pass

    return entries


def find_main_blocks(source_dir: Path) -> list[str]:
    """Search Python files for `if __name__ == "__main__"` blocks.

    Args:
        source_dir: Directory to search for Python files.

    Returns:
        List of module paths (relative to project root) with main blocks.
    """
    mains: list[str] = []

    for py_file in source_dir.rglob("*.py"):
        try:
            with open(py_file, encoding="utf-8") as f:
                tree = ast.parse(f.read())

            for node in ast.walk(tree):
                if isinstance(node, ast.If):
                    # Check for: if __name__ == "__main__"
                    if isinstance(node.test, ast.Compare):
                        if len(node.test.ops) > 0 and isinstance(
                            node.test.ops[0], ast.Eq
                        ):
                            left = node.test.left
                            right = (
                                node.test.comparators[0]
                                if node.test.comparators
                                else None
                            )

                            if (
                                isinstance(left, ast.Name)
                                and left.id == "__name__"
                                and isinstance(right, ast.Constant)
                                and right.value == "__main__"
                            ) or (
                                isinstance(right, ast.Name)
                                and right.id == "__name__"
                                and isinstance(left, ast.Constant)
                                and left.value == "__main__"
                            ):
                                # Get relative path
                                rel_path = py_file.relative_to(source_dir.parent)
                                module = (
                                    str(rel_path)
                                    .replace(".py", "")
                                    .replace(os.sep, "/")
                                )
                                if module not in mains:
                                    mains.append(module)
        except (SyntaxError, OSError):
            pass

    return mains


def detect_cli_modules(source_dir: Path) -> list[str]:
    """Find CLI-like modules by naming conventions.

    Args:
        source_dir: Directory to search.

    Returns:
        List of module paths matching CLI naming patterns.
    """
    cli_modules: list[str] = []
    cli_names = {"cli.py", "main.py", "app.py", "__main__.py"}

    for py_file in source_dir.rglob("*.py"):
        if py_file.name in cli_names:
            rel_path = py_file.relative_to(source_dir.parent)
            module = str(rel_path).replace(".py", "").replace(os.sep, "/")
            if module not in cli_modules:
                cli_modules.append(module)

    return cli_modules


def detect_entry_point(project_path: str) -> str | None:
    """Auto-detect the main entry point for the project.

    Strategy (in priority order):
    1. Extract from pyproject.toml [project.scripts] (first entry)
    2. Find modules with __main__ blocks
    3. Detect common CLI modules (cli.py, main.py, app.py)
    4. Return None if nothing found (non-CLI project)

    Args:
        project_path: Root directory of the project.

    Returns:
        Path to entry point (e.g., "src/myapp/cli.py") or None.
    """
    # Strategy 1: pyproject.toml scripts
    py_entries = extract_entry_points_from_pyproject(project_path)
    if py_entries:
        # Return first script's value (module:function format)
        first_value = next(iter(py_entries.values()))
        # Extract module part before colon if present
        module_part = first_value.split(":")[0] if ":" in first_value else first_value
        return module_part.replace(".", "/") + ".py"

    # Strategy 2 & 3: Search in project sources
    sources = find_project_sources(project_path)
    for source_dir in sources:
        if not source_dir.exists():
            continue

        # Try to find __main__ blocks first (highest priority)
        mains = find_main_blocks(source_dir)
        if mains:
            # Prefer __main__.py, then cli.py, then the first one found
            for candidate in ["__main__", "cli", "main", "app"]:
                for main in mains:
                    if candidate in main:
                        return main + ".py"
            return mains[0] + ".py"

        # Try to find CLI modules by naming
        cli_mods = detect_cli_modules(source_dir)
        if cli_mods:
            # Prefer cli.py, then main.py, then app.py, then __main__.py
            for candidate in ["cli", "main", "app", "__main__"]:
                for mod in cli_mods:
                    if mod.endswith(candidate):
                        return mod + ".py"
            return cli_mods[0] + ".py"

    return None


def list_potential_entry_points(project_path: str) -> list[str]:
    """List all potential entry points found in the project.

    Useful for users to choose from when auto-detection is ambiguous.

    Args:
        project_path: Root directory of the project.

    Returns:
        List of potential entry point paths.
    """
    candidates: list[str] = []

    # Add pyproject.toml entries
    py_entries = extract_entry_points_from_pyproject(project_path)
    for value in py_entries.values():
        if ":" in value:
            module_part = value.split(":")[0]
            candidates.append(module_part.replace(".", "/") + ".py")

    # Add discovered sources
    sources = find_project_sources(project_path)
    for source_dir in sources:
        if not source_dir.exists():
            continue

        # Add __main__ blocks
        mains = find_main_blocks(source_dir)
        candidates.extend(main + ".py" for main in mains)

        # Add CLI modules
        cli_mods = detect_cli_modules(source_dir)
        candidates.extend(mod + ".py" for mod in cli_mods)

    # Remove duplicates and sort
    return sorted(set(candidates))
