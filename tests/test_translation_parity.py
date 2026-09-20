"""Repo-wide key-path parity between strings.json and every translations/*.json file."""
import json
from pathlib import Path

COMPONENT_ROOT = Path(__file__).parents[1] / "custom_components/miele_lan"


def _leaf_paths(node: object, prefix: str = "") -> set[str]:
    if not isinstance(node, dict):
        return {prefix}
    paths: set[str] = set()
    for key, value in node.items():
        full = f"{prefix}.{key}" if prefix else key
        paths |= _leaf_paths(value, full)
    return paths


def _translation_files() -> dict[str, Path]:
    files = {"strings.json": COMPONENT_ROOT / "strings.json"}
    for path in sorted((COMPONENT_ROOT / "translations").glob("*.json")):
        files[f"translations/{path.name}"] = path
    return files


def test_all_translation_files_have_matching_key_paths():
    files = _translation_files()
    paths_by_file = {
        name: _leaf_paths(json.loads(path.read_text(encoding="utf-8")))
        for name, path in files.items()
    }
    all_paths: set[str] = set().union(*paths_by_file.values())

    failures = []
    for key_path in sorted(all_paths):
        present_in = [name for name, paths in paths_by_file.items() if key_path in paths]
        missing_from = [name for name in paths_by_file if name not in present_in]
        for name in missing_from:
            failures.append(
                f"{name}: missing key path '{key_path}' (present in {', '.join(present_in)})"
            )
    assert not failures, "\n" + "\n".join(failures)
