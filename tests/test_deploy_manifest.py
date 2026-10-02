"""Static guards for what gets deployed to the Raspberry Pi.

install.sh and update.sh copy exactly the files listed in deploy-files.txt.
A module or template missing from that list passes every other test but
crashes on the device, which is how the audit found a broken install. These
checks also pin the dependency, service-user and sudoers fixes in place.
"""

import ast
import re
import shutil
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, SUDOERS_PATH, load_sudoers_commands

MANIFEST = REPO_ROOT / "deploy-files.txt"
DEPLOY_SCRIPTS = ("install.sh", "update.sh")
HARDWARE_OR_UNUSED_PACKAGES = {
    "genkit", "genkit-plugin-google-genai", "rpi.gpio", "picamera2",
}


def manifest_entries() -> list[str]:
    lines = (line.strip() for line in MANIFEST.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def local_imports(module_path) -> set[str]:
    """Names of repository top-level modules imported by `module_path`."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return {name for name in names if (REPO_ROOT / f"{name}.py").exists()}


def runtime_modules() -> set[str]:
    """app.py plus every repository module reachable from it through imports."""
    found, queue = set(), ["app.py"]
    while queue:
        current = queue.pop()
        if current not in found:
            found.add(current)
            queue.extend(f"{name}.py" for name in local_imports(REPO_ROOT / current))
    return found


def requirement_names(path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(re.split(r"[<>=!~;\[\s]", line, maxsplit=1)[0].lower())
    return names


def read_script(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


class TestDeployManifest:
    def test_lists_only_existing_files_without_globs(self):
        # Act
        entries = manifest_entries()

        # Assert
        assert entries, "deploy-files.txt is empty"
        for entry in entries:
            assert "*" not in entry and "?" not in entry, entry
            assert (REPO_ROOT / entry).is_file(), f"{entry} does not exist"

    def test_every_module_reachable_from_app_is_deployed(self):
        # Act
        modules = runtime_modules()

        # Assert
        assert {"app.py", "ai_provider.py", "constants.py"} <= modules
        missing = modules - set(manifest_entries())
        assert not missing, f"not deployed: {sorted(missing)}"

    def test_every_template_is_deployed(self):
        # Arrange
        templates = {
            p.relative_to(REPO_ROOT).as_posix()
            for p in (REPO_ROOT / "templates").glob("*.html")
        }

        # Act / Assert
        assert templates
        assert templates <= set(manifest_entries())

    def test_every_static_file_is_deployed(self):
        # Arrange — the portal is served offline from these files.
        static_files = {
            p.relative_to(REPO_ROOT).as_posix()
            for p in (REPO_ROOT / "static").rglob("*") if p.is_file()
        }

        # Act / Assert
        assert static_files
        assert static_files <= set(manifest_entries())

    def test_runtime_data_and_updater_are_deployed(self):
        # Act / Assert
        assert {
            "default_config.json", "VERSION", "update.sh", "requirements.txt",
        } <= set(manifest_entries())

    def test_runtime_state_is_never_deployed(self):
        # Act / Assert — overwriting these would wipe the user's keys/photos.
        for entry in manifest_entries():
            assert entry != "config.json"
            assert not entry.startswith("photos/")


class TestDeployScripts:
    @pytest.mark.parametrize("script", DEPLOY_SCRIPTS)
    def test_copies_runtime_files_from_the_manifest(self, script):
        # Arrange
        text = read_script(script)

        # Act / Assert
        assert 'done < "${src}/deploy-files.txt"' in text
        assert re.search(
            r'^\s*deploy_runtime_files\s+\S+\s+"\$\{INSTALL_DIR\}"\s*$', text, re.M
        )
        # No hand-maintained copy of the list next to the manifest.
        assert not re.search(r"^\s*(cp|install)\b.*\.(py|html)\b", text, re.M)

    @pytest.mark.parametrize("script", DEPLOY_SCRIPTS)
    def test_installs_a_validated_sudoers_whitelist(self, script):
        # Arrange
        text = read_script(script)

        # Act / Assert
        assert "visudo -cf" in text
        assert re.search(r"^\s*install_sudoers\s+\S*pixelpotion\.sudoers", text, re.M)

    @pytest.mark.parametrize("script", DEPLOY_SCRIPTS)
    def test_runs_in_strict_mode_and_hands_files_to_the_service_user(self, script):
        # Arrange
        text = read_script(script)

        # Act / Assert
        assert re.search(r"^set -euo pipefail$", text, re.M)
        assert 'chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"' in text


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="needs a POSIX bash (runs in CI)",
)
class TestUpdateVersionComparison:
    @staticmethod
    def update_needed(latest: str, current: str) -> bool:
        text = read_script("update.sh")
        helpers = "".join(
            re.search(rf"^{name}\(\) \{{.*?^\}}\n", text, re.M | re.S).group(0)
            for name in ("normalize_version", "version_is_newer")
        )
        script = helpers + (
            'version_is_newer "$(normalize_version "$1")" "$(normalize_version "$2")"'
        )
        result = subprocess.run(
            ["bash", "-c", script, "update.sh", latest, current], check=False
        )
        return result.returncode == 0

    @pytest.mark.parametrize("latest, current, expected", [
        ("v2.1.0", "v2.0.2", True),
        ("v2.1.0", "2.0.2", True),
        ("v2.0.10", "v2.0.9", True),
        ("v2.0.2", "v2.0.2", False),
        ("v2.0.2", "2.0.2", False),
        ("v2.0.1", "v2.0.2", False),
    ])
    def test_updates_only_to_strictly_newer_releases(self, latest, current, expected):
        # Act / Assert
        assert self.update_needed(latest, current) is expected


class TestRequirements:
    def test_runtime_requirements_declare_the_ai_sdk_only_once(self):
        # Act
        names = requirement_names(REPO_ROOT / "requirements.txt")

        # Assert — hardware libraries come from apt, genkit is unused.
        assert "google-genai" in names
        assert not names & HARDWARE_OR_UNUSED_PACKAGES

    def test_dev_requirements_stay_installable_off_device(self):
        # Act
        names = requirement_names(REPO_ROOT / "requirements-dev.txt")

        # Assert
        assert "pytest" in names
        assert not names & (HARDWARE_OR_UNUSED_PACKAGES | {"google-genai"})


class TestServiceUnit:
    def test_service_does_not_run_as_root(self):
        # Arrange — an absent User= also means root.
        unit = (REPO_ROOT / "config" / "pixelpotion.service").read_text(encoding="utf-8")

        # Act
        users = re.findall(r"^User=(\S+)\s*$", unit, re.M)

        # Assert
        assert users == ["pi"]


class TestSudoersWhitelistFile:
    def test_commands_are_exact_absolute_command_lines(self):
        # Act
        commands = load_sudoers_commands()

        # Assert — in sudoers a bare command would allow any arguments.
        assert commands
        for command in commands:
            binary, *args = command.split()
            assert binary.startswith("/"), command
            assert args, f"{command} has no fixed arguments"
            assert "*" not in command and "ALL" not in args, command

    def test_file_has_no_wildcards_and_grants_only_the_alias(self):
        # Arrange
        rules = [
            line for line in SUDOERS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        # Act
        user_specs = [line for line in rules if not line.startswith((" ", "Cmnd_Alias"))]

        # Assert
        assert not any("*" in line for line in rules)
        assert user_specs == ["pi ALL=(root) NOPASSWD: PIXELPOTION_NET"]
