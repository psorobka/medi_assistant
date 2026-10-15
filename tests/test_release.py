"""Ensure installable release metadata and frontend cache version agree."""

import json
import tomllib
from pathlib import Path

from custom_components.medi_assistant.frontend import CARD_URL

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_is_consistent():
    manifest = json.loads((ROOT / "custom_components/medi_assistant/manifest.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    version = manifest["version"]
    assert project["version"] == version
    assert CARD_URL.endswith("?v=" + version)
    assert "## " + version in (ROOT / "CHANGELOG.md").read_text()
