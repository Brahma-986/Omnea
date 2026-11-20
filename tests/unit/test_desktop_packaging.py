"""Unit tests for Desktop Packaging and CI Workflows.

Verifies:
1. GitHub Actions workflow `.github/workflows/desktop.yml` structure, triggers, matrix OS jobs,
   Node.js version, caching, build commands, artifact upload, and GitHub release step.
2. `desktop/electron-builder.yml` configuration: app identity, `procure` protocol registration,
   platform targets (Windows NSIS/portable, macOS DMG/zip, Linux AppImage/deb), icons, and security settings.
3. `desktop/package.json` metadata, scripts, and build target alignment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "desktop.yml"
BUILDER_CONFIG_PATH = REPO_ROOT / "desktop" / "electron-builder.yml"
PACKAGE_JSON_PATH = REPO_ROOT / "desktop" / "package.json"


@pytest.fixture(scope="module")
def workflow_yaml() -> dict[str, Any]:
    assert WORKFLOW_PATH.is_file(), f"Workflow file not found at {WORKFLOW_PATH}"
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "Invalid YAML in desktop.yml"
    return data


@pytest.fixture(scope="module")
def builder_config() -> dict[str, Any]:
    assert BUILDER_CONFIG_PATH.is_file(), (
        f"electron-builder config not found at {BUILDER_CONFIG_PATH}"
    )
    with open(BUILDER_CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "Invalid YAML in electron-builder.yml"
    return data


@pytest.fixture(scope="module")
def package_json() -> dict[str, Any]:
    assert PACKAGE_JSON_PATH.is_file(), f"package.json not found at {PACKAGE_JSON_PATH}"
    with open(PACKAGE_JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict), "Invalid JSON in package.json"
    return data


# ============================================================================
# GitHub Actions Workflow Tests (.github/workflows/desktop.yml)
# ============================================================================


def test_workflow_metadata_and_triggers(workflow_yaml: dict[str, Any]) -> None:
    """Workflow must be named correctly and triggered on tags, PRs, and workflow_dispatch."""
    assert workflow_yaml.get("name") == "Desktop Application Build & Release"

    triggers = workflow_yaml.get("on") or workflow_yaml.get(True)
    assert triggers is not None, "Workflow has no 'on' triggers defined"

    # 1. Push on tags 'v*'
    assert "push" in triggers, "Missing 'push' trigger"
    push_config = triggers["push"]
    assert "tags" in push_config, "Missing 'tags' filter in push trigger"
    assert any("v*" in tag for tag in push_config["tags"]), "Tags must include 'v*'"

    # 2. Pull Request touching desktop/**
    assert "pull_request" in triggers, "Missing 'pull_request' trigger"
    pr_config = triggers["pull_request"]
    assert "paths" in pr_config, "Missing 'paths' filter in pull_request trigger"
    assert any("desktop/**" in p for p in pr_config["paths"]), "PR paths must include 'desktop/**'"

    # 3. workflow_dispatch manual trigger
    assert "workflow_dispatch" in triggers, "Missing 'workflow_dispatch' trigger"


def test_workflow_matrix_os_strategy(workflow_yaml: dict[str, Any]) -> None:
    """Matrix must build on windows-latest, macos-latest, and ubuntu-latest."""
    jobs = workflow_yaml.get("jobs", {})
    assert "build" in jobs, "Missing 'build' job in workflow"
    build_job = jobs["build"]

    strategy = build_job.get("strategy", {})
    matrix = strategy.get("matrix", {})
    os_list = matrix.get("os", [])
    assert "windows-latest" in os_list, "Matrix missing windows-latest"
    assert "macos-latest" in os_list, "Matrix missing macos-latest"
    assert "ubuntu-latest" in os_list, "Matrix missing ubuntu-latest"

    # Verify include configurations for target installers
    includes = matrix.get("include", [])
    include_os_map = {inc.get("os"): inc for inc in includes if isinstance(inc, dict)}

    assert "windows-latest" in include_os_map
    assert "macos-latest" in include_os_map
    assert "ubuntu-latest" in include_os_map

    # Check Windows installer extension
    win_inc = include_os_map["windows-latest"]
    assert ".exe" in win_inc.get("installer_exts", "")

    # Check macOS installer extensions
    mac_inc = include_os_map["macos-latest"]
    assert ".dmg" in mac_inc.get("installer_exts", "")
    assert ".zip" in mac_inc.get("installer_exts", "")

    # Check Linux installer extensions
    linux_inc = include_os_map["ubuntu-latest"]
    assert ".AppImage" in linux_inc.get("installer_exts", "")
    assert ".deb" in linux_inc.get("installer_exts", "")


def test_workflow_checkout_and_node_setup(workflow_yaml: dict[str, Any]) -> None:
    """Workflow steps must checkout repo and setup Node.js v20 with dependency caching."""
    steps = workflow_yaml["jobs"]["build"]["steps"]

    # 1. Checkout
    checkout_step = next((s for s in steps if "checkout" in s.get("uses", "").lower()), None)
    assert checkout_step is not None, "Missing actions/checkout step"

    # 2. Setup Node.js v20
    node_step = next((s for s in steps if "setup-node" in s.get("uses", "").lower()), None)
    assert node_step is not None, "Missing actions/setup-node step"
    with_clause = node_step.get("with", {})
    node_version = str(with_clause.get("node-version", ""))
    assert "20" in node_version, f"Expected Node.js 20, got {node_version}"

    # 3. Cache dependencies
    cache_configured = with_clause.get("cache") == "npm" or any(
        "cache" in s.get("uses", "").lower() for s in steps
    )
    assert cache_configured, "Expected npm dependency caching to be configured"


def test_workflow_dependencies_install_and_tests(workflow_yaml: dict[str, Any]) -> None:
    """Must run npm ci/install and npm test inside desktop/."""
    steps = workflow_yaml["jobs"]["build"]["steps"]

    # Install dependencies step
    install_step = next(
        (
            s
            for s in steps
            if "npm" in s.get("run", "")
            and ("install" in s.get("run", "") or "ci" in s.get("run", ""))
        ),
        None,
    )
    assert install_step is not None, "Missing npm install/ci step"
    assert install_step.get("working-directory") == "desktop", "Install step must run in desktop/"

    # Test step
    test_step = next((s for s in steps if "npm test" in s.get("run", "")), None)
    assert test_step is not None, "Missing npm test step"
    assert test_step.get("working-directory") == "desktop", "Test step must run in desktop/"


def test_workflow_electron_builder_and_artifacts(workflow_yaml: dict[str, Any]) -> None:
    """Must execute electron-builder --publish never and upload build artifacts."""
    steps = workflow_yaml["jobs"]["build"]["steps"]

    # Build installers
    build_step = next((s for s in steps if "electron-builder" in s.get("run", "")), None)
    assert build_step is not None, "Missing electron-builder step"
    assert "--publish never" in build_step.get("run", ""), (
        "electron-builder must include '--publish never'"
    )
    assert build_step.get("working-directory") == "desktop", "Build step must run in desktop/"

    # Artifact upload
    upload_step = next((s for s in steps if "upload-artifact" in s.get("uses", "")), None)
    assert upload_step is not None, "Missing upload-artifact step"
    upload_path = upload_step.get("with", {}).get("path", "")

    expected_extensions = [".exe", ".dmg", ".zip", ".AppImage", ".deb"]
    for ext in expected_extensions:
        assert ext in upload_path, f"Upload artifact paths missing {ext}"


def test_workflow_release_on_tag(workflow_yaml: dict[str, Any]) -> None:
    """Must attach installers to GitHub Release via softprops/action-gh-release on tag push."""
    steps = workflow_yaml["jobs"]["build"]["steps"]

    release_step = next((s for s in steps if "action-gh-release" in s.get("uses", "")), None)
    assert release_step is not None, "Missing softprops/action-gh-release step"

    # Condition: only on tag ref
    if_cond = release_step.get("if", "")
    assert "refs/tags/v" in if_cond or "tags" in if_cond, (
        "Release step must be conditioned on tag push"
    )

    files_clause = release_step.get("with", {}).get("files", "")
    expected_extensions = [".exe", ".dmg", ".zip", ".AppImage", ".deb"]
    for ext in expected_extensions:
        assert ext in files_clause, f"Release files list missing {ext}"


# ============================================================================
# Electron-Builder Configuration Tests (desktop/electron-builder.yml)
# ============================================================================


def test_electron_builder_app_identity(builder_config: dict[str, Any]) -> None:
    """App ID, Product Name, and build directories must be strictly set."""
    assert builder_config.get("appId") == "ai.omnea.procure"
    assert builder_config.get("productName") == "Omnea ProcureOS"

    dirs = builder_config.get("directories", {})
    assert dirs.get("output") == "dist"
    assert dirs.get("buildResources") == "assets"

    files = builder_config.get("files", [])
    assert any("src" in f for f in files)
    assert any("assets" in f for f in files)
    assert any("package.json" in f for f in files)


def test_electron_builder_deep_link_protocol(builder_config: dict[str, Any]) -> None:
    """Custom protocol 'procure' must be registered for deep linking."""
    protocols = builder_config.get("protocols", [])
    assert isinstance(protocols, list) and len(protocols) > 0, "No protocols registered"

    procure_proto = next(
        (p for p in protocols if "procure" in p.get("schemes", [])),
        None,
    )
    assert procure_proto is not None, "Protocol 'procure' scheme is not registered"
    assert "Omnea Procure" in procure_proto.get("name", "")


def _extract_target_names(target_config: Any) -> list[str]:
    """Helper to extract target names from list of strings or list of dicts."""
    if isinstance(target_config, list):
        names = []
        for t in target_config:
            if isinstance(t, str):
                names.append(t)
            elif isinstance(t, dict) and "target" in t:
                names.append(t["target"])
        return names
    if isinstance(target_config, str):
        return [target_config]
    return []


def test_electron_builder_windows_targets(builder_config: dict[str, Any]) -> None:
    """Windows targets must include NSIS installer and portable executable."""
    win = builder_config.get("win", {})
    targets = _extract_target_names(win.get("target"))
    assert "nsis" in targets, "Windows targets must include 'nsis'"
    assert "portable" in targets, "Windows targets must include 'portable'"
    assert "icon" in win, "Windows configuration must specify an icon"

    # NSIS detailed settings
    nsis = builder_config.get("nsis", {})
    assert nsis.get("oneClick") is False, "Enterprise NSIS installer should allow custom directory"
    assert nsis.get("allowToChangeInstallationDirectory") is True


def test_electron_builder_macos_targets(builder_config: dict[str, Any]) -> None:
    """macOS targets must include DMG and ZIP with security options."""
    mac = builder_config.get("mac", {})
    targets = _extract_target_names(mac.get("target"))
    assert "dmg" in targets, "macOS targets must include 'dmg'"
    assert "zip" in targets, "macOS targets must include 'zip'"
    assert "icon" in mac, "macOS configuration must specify an icon"
    assert mac.get("category") == "public.app-category.business"
    assert mac.get("hardenedRuntime") is True, (
        "macOS builds require hardenedRuntime for notarization"
    )


def test_electron_builder_linux_targets(builder_config: dict[str, Any]) -> None:
    """Linux targets must include AppImage and deb with desktop file integration."""
    linux = builder_config.get("linux", {})
    targets = _extract_target_names(linux.get("target"))
    assert "AppImage" in targets, "Linux targets must include 'AppImage'"
    assert "deb" in targets, "Linux targets must include 'deb'"
    assert "icon" in linux, "Linux configuration must specify an icon"

    desktop_config = linux.get("desktop", {})
    assert "procure" in desktop_config.get("MimeType", ""), (
        "Desktop file must register x-scheme-handler/procure"
    )


# ============================================================================
# Package.json & Configuration Alignment Tests
# ============================================================================


def test_desktop_package_json_integrity(package_json: dict[str, Any]) -> None:
    """desktop/package.json must contain required metadata, scripts, and build section."""
    assert package_json.get("name") == "omnea-procure-desktop"
    assert package_json.get("version") == "1.0.0"
    assert package_json.get("main") == "src/main.js"

    scripts = package_json.get("scripts", {})
    assert "start" in scripts
    assert "pack" in scripts
    assert "build" in scripts
    assert "test" in scripts

    deps = package_json.get("dependencies", {})
    dev_deps = package_json.get("devDependencies", {})
    assert "electron-updater" in deps or "electron-updater" in dev_deps
    assert "electron" in dev_deps or "electron" in deps
    assert "electron-builder" in dev_deps or "electron-builder" in deps
