"""The manifest is a contract: declared capabilities, schema keys and reality agree."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

import house_rules
from house_rules import schemas
from house_rules.settings import SCHEMA_KEYS

PLUGIN_DIR = Path(__file__).resolve().parents[1]
PLUGIN_YAML = PLUGIN_DIR / "plugin.yaml"


@pytest.fixture(scope="module")
def manifest():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(PLUGIN_YAML.read_text(encoding="utf-8"))


def test_every_config_key_is_declared_in_both_places(manifest):
    assert set(manifest["config_schema"]) == set(SCHEMA_KEYS)


def test_config_schema_defaults_match_the_settings_defaults(manifest):
    from house_rules.settings import Settings

    defaults = Settings()
    for key, spec in manifest["config_schema"].items():
        assert spec.get("default") == getattr(defaults, key), key
        assert spec.get("label"), key
        assert spec.get("description"), key


def test_declared_tools_match_the_schemas_we_register(manifest):
    assert set(manifest["provides_tools"]) == {schema["name"] for schema in schemas.ALL}
    assert len(manifest["provides_tools"]) == len(set(manifest["provides_tools"]))


def test_declared_hooks_match_the_hooks_we_register(manifest):
    assert set(manifest["provides_hooks"]) == {"pre_tool_call"}


def test_manifest_identity_fields(manifest):
    assert manifest["manifest_version"] == 2
    assert manifest["api_version"] == 1
    assert manifest["license"] == "MIT"
    assert manifest["author"] == "kyssta"
    assert manifest["homepage"] == "https://github.com/okyssta/hermes-house-rules"
    assert isinstance(manifest["version"], str)
    assert manifest["requires_hermes"].startswith(">=")
    assert not manifest.get("python_dependencies")


def test_description_ends_with_a_disclosure(manifest):
    description = manifest["description"].strip()
    assert "Disclosure —" in description
    assert description.split("Disclosure —")[-1].strip()


def test_every_tool_schema_is_usable(manifest):
    for schema in schemas.ALL:
        assert schema["name"] in manifest["provides_tools"]
        assert schema["description"].endswith(".")
        params = schema["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict) and params["properties"]
        for name, spec in params["properties"].items():
            assert spec.get("type") or spec.get("enum"), (schema["name"], name)
            assert spec.get("description"), (schema["name"], name)
        for required in params.get("required") or []:
            assert required in params["properties"]


def test_rule_add_schema_matches_the_handler_signature():
    props = schemas.RULE_ADD["parameters"]["properties"]
    assert set(props) == {"pattern", "tool", "action", "scope", "message", "name", "match_on"}
    assert props["action"]["enum"] == ["block", "approve", "warn"]
    assert props["scope"]["enum"] == ["project", "global"]
    assert props["match_on"]["enum"] == ["command", "path", "url", "all"]
    assert schemas.RULE_ADD["parameters"]["required"] == ["pattern"]


class RecordingContext:
    plugin_config: dict = {}
    profile_name = "test"

    def __init__(self):
        self.tools: list[str] = []
        self.hooks: list[str] = []
        self.commands: list[str] = []
        self.skills: list[str] = []

    def register_tool(self, name, *args, **kwargs):
        self.tools.append(str(name))

    def register_hook(self, hook_name, callback):
        self.hooks.append(str(hook_name))

    def register_command(self, name, *args, **kwargs):
        self.commands.append(str(name))

    def register_skill(self, name, path):
        self.skills.append(str(name))

    def get_config(self, key, default=None):
        return default


def test_register_registers_exactly_what_the_manifest_declares(env):
    ctx = RecordingContext()
    house_rules.register(ctx)
    assert sorted(ctx.tools) == sorted({schema["name"] for schema in schemas.ALL})
    assert ctx.hooks == ["pre_tool_call"]
    assert ctx.commands == ["rules"]
    assert ctx.skills == ["house_rules"]


def test_register_is_idempotent_and_survives_a_thin_context(env):
    class Thin(RecordingContext):
        def register_skill(self, *args, **kwargs):
            raise RuntimeError("no skills here")

        def get_config(self, key, default=None):
            raise RuntimeError("no config here")

    house_rules.reset_settings_cache()
    ctx = Thin()
    house_rules.register(ctx)  # must not raise
    assert ctx.hooks == ["pre_tool_call"]


def test_bundled_skill_exists_with_frontmatter():
    skill = PLUGIN_DIR / "skills" / "house_rules" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    header = text.split("---", 2)[1]
    assert "name: house_rules" in header
    assert "description:" in header
    assert len(text.splitlines()) > 40
    assert "rule_add" in text and "rule_test" in text


def _python_sources() -> list[Path]:
    return sorted(
        path
        for path in PLUGIN_DIR.rglob("*.py")
        if ".venv" not in path.parts and "tests" not in path.parts
    )


def test_no_network_or_subprocess_imports():
    forbidden = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "subprocess", "asyncio"}
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                assert name not in forbidden, f"{path.name} imports {name}"


def test_no_eval_exec_or_compile_of_rule_text():
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "compile", "__import__"}, f"{path.name}: {node.func.id}"
            if isinstance(node, ast.Attribute) and node.attr in {"system", "popen", "spawn"}:
                raise AssertionError(f"{path.name}: os.{node.attr}")


def test_reads_no_credentials_or_env_files():
    """No path literal anywhere reads a dotenv or a Hermes auth file."""
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            literal = node.value
            assert "auth.json" not in literal, f"{path.name} names auth.json"
            assert not re.search(r"(^|[/\\])\.env$", literal), f"{path.name} names a .env file"
            assert "credentials" not in literal.lower() or "no credentials" in literal.lower(), path.name


def test_module_imports_are_stdlib_only():
    stdlib = set(sys.stdlib_module_names)
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root in stdlib or root in {"house_rules", "hermes_constants"}, (path.name, root)
