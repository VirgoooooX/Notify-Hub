import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def get_current_version(project_root: Path) -> str:
    pyproject_path = project_root / "pyproject.toml"
    if not pyproject_path.is_file():
        print(f"Error: {pyproject_path} not found.")
        sys.exit(1)

    content = pyproject_path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        print("Error: Could not find version inside pyproject.toml.")
        sys.exit(1)
    return match.group(1)


def bump_version_string(current_version: str, bump_type: str) -> str:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", current_version)
    if not match:
        print(f"Error: Version string {current_version!r} is not semver compliant.")
        sys.exit(1)

    major, minor, patch, extra = match.groups()
    major, minor, patch = int(major), int(minor), int(patch)

    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        # custom
        return bump_type


def update_pyproject(project_root: Path, new_version: str) -> None:
    pyproject_path = project_root / "pyproject.toml"
    content = pyproject_path.read_text(encoding="utf-8")
    new_content = re.sub(
        r'(^version\s*=\s*")([^"]+)(")', f"\\g<1>{new_version}\\g<3>", content, flags=re.MULTILINE
    )
    pyproject_path.write_text(new_content, encoding="utf-8")
    print(f"Updated: {pyproject_path.name}")


def update_backend_main(project_root: Path, new_version: str) -> None:
    main_path = project_root / "backend" / "app" / "main.py"
    if not main_path.is_file():
        print(f"Warning: {main_path} not found. Skipping backend version bump.")
        return
    content = main_path.read_text(encoding="utf-8")
    # Replace version="0.3.0" inside FastAPI initialization
    new_content = re.sub(
        r'(version\s*=\s*")([^"]+)(")', f"\\g<1>{new_version}\\g<3>", content, count=1
    )
    main_path.write_text(new_content, encoding="utf-8")
    print(f"Updated: backend/app/main.py")


def update_frontend_package(project_root: Path, new_version: str) -> None:
    pkg_path = project_root / "frontend" / "package.json"
    if not pkg_path.is_file():
        print(f"Warning: {pkg_path} not found. Skipping frontend package version bump.")
        return

    data = json.loads(pkg_path.read_text(encoding="utf-8"))
    data["version"] = new_version
    pkg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Updated: frontend/package.json")

    # Try sync package-lock.json using npm if available
    lock_path = project_root / "frontend" / "package-lock.json"
    if lock_path.is_file():
        try:
            # First try modifying via json directly to make it fallback-safe
            lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
            if "version" in lock_data:
                lock_data["version"] = new_version
            if "packages" in lock_data and "" in lock_data["packages"]:
                lock_data["packages"][""]["version"] = new_version
            lock_path.write_text(
                json.dumps(lock_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"Updated: frontend/package-lock.json (JSON fallback)")

            # Call npm install to ensure clean formatting
            subprocess.run(
                ["npm", "install", "--package-lock-only"],
                cwd=str(project_root / "frontend"),
                shell=True,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"Warning: Failed to format package-lock.json with npm: {exc}")


def update_frontend_files_version(project_root: Path, new_version: str) -> None:
    # 1. Update LoginView.vue
    login_path = project_root / "frontend" / "src" / "views" / "LoginView.vue"
    if login_path.is_file():
        content = login_path.read_text(encoding="utf-8")
        new_content = re.sub(
            r'(NOTIFY HUB / RELEASE\s+)([0-9.]+)',
            f"\\g<1>{new_version}",
            content
        )
        login_path.write_text(new_content, encoding="utf-8")
        print(f"Updated: frontend/src/views/LoginView.vue")

    # 2. Update SettingsView.vue
    settings_path = project_root / "frontend" / "src" / "views" / "SettingsView.vue"
    if settings_path.is_file():
        content = settings_path.read_text(encoding="utf-8")
        new_content = re.sub(
            r"(version:\s*')([0-9.]+)(')",
            f"\\g<1>{new_version}\\g<3>",
            content
        )
        settings_path.write_text(new_content, encoding="utf-8")
        print(f"Updated: frontend/src/views/SettingsView.vue")

    # 3. Update AppLayout.vue
    layout_path = project_root / "frontend" / "src" / "layouts" / "AppLayout.vue"
    if layout_path.is_file():
        content = layout_path.read_text(encoding="utf-8")
        new_content = re.sub(
            r'(OPERATIONS\s+/\s+)([0-9.]+)',
            f"\\g<1>{new_version}",
            content
        )
        layout_path.write_text(new_content, encoding="utf-8")
        print(f"Updated: frontend/src/layouts/AppLayout.vue")

def git_operations(project_root: Path, new_version: str, auto_push: bool) -> None:
    tag_name = f"v{new_version}"
    commit_msg = f"bump: release {tag_name}"

    try:
        # Check git status
        subprocess.run(["git", "--version"], check=True, stdout=subprocess.DEVNULL)
    except Exception:
        print("Warning: git is not installed or not in PATH. Skipping git commits.")
        return

    # 1. Format backend using ruff BEFORE staging
    subprocess.run(
        ["uv", "run", "ruff", "format", "backend/app/main.py"],
        cwd=str(project_root),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 2. Stage all version-modified files together
    files_to_stage = [
        "pyproject.toml",
        "backend/app/main.py",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/src/views/LoginView.vue",
        "frontend/src/views/SettingsView.vue",
        "frontend/src/layouts/AppLayout.vue",
    ]
    for rel_path in files_to_stage:
        file_path = project_root / rel_path
        if file_path.is_file():
            subprocess.run(["git", "add", rel_path], cwd=str(project_root), check=True)

    # 3. Create the unified commit
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=str(project_root), check=True)
    print(f"\nCreated Git Commit: {commit_msg}")

    # 4. Tag the commit
    subprocess.run(
        ["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"],
        cwd=str(project_root),
        check=True,
    )
    print(f"Created Git Tag:    {tag_name}")

    if auto_push:
        print(f"\nPushing branches and tags to origin...")
        subprocess.run(["git", "push", "origin", "main"], cwd=str(project_root), check=True)
        subprocess.run(["git", "push", "origin", tag_name], cwd=str(project_root), check=True)
        print("Successfully pushed to GitHub. Release workflow triggered!")
    else:
        print(f"\nTo trigger the automatic release pipeline, run:")
        print(f"  git push origin main && git push origin {tag_name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bump application version and release tags.")
    parser.add_argument(
        "--type",
        choices=["patch", "minor", "major"],
        help="Type of semver bump (patch, minor, major)",
    )
    parser.add_argument("--version", help="Specify a custom version directly")
    parser.add_argument("--push", action="store_true", help="Automatically push tags to origin")

    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    current_version = get_current_version(project_root)

    print(f"--- Notify Hub Version Bump Tool ---")
    print(f"Current version: {current_version}")

    new_version = None
    if args.version:
        new_version = args.version
    elif args.type:
        new_version = bump_version_string(current_version, args.type)
    else:
        # Prompt user interactively
        v_patch = bump_version_string(current_version, "patch")
        v_minor = bump_version_string(current_version, "minor")
        v_major = bump_version_string(current_version, "major")

        print("\nSelect the new version:")
        print(f"  1) patch: {current_version} -> {v_patch} (Bug fixes)")
        print(f"  2) minor: {current_version} -> {v_minor} (Features, updates)")
        print(f"  3) major: {current_version} -> {v_major} (Breaking changes)")
        print(f"  4) custom: Enter version manually")

        choice = input("\nEnter choice (1-4): ").strip()
        if choice == "1":
            new_version = v_patch
        elif choice == "2":
            new_version = v_minor
        elif choice == "3":
            new_version = v_major
        elif choice == "4":
            new_version = input("Enter custom version (e.g. 0.4.0): ").strip()
        else:
            print("Invalid choice. Exiting.")
            sys.exit(1)

    if not new_version:
        print("Error: Target version could not be resolved.")
        sys.exit(1)

    # Validate version format
    if not re.match(r"^\d+\.\d+\.\d+(?:-.*)?$", new_version):
        print(f"Error: Target version {new_version!r} is invalid.")
        sys.exit(1)

    print(f"\nBumping version: {current_version} -> {new_version}...")

    # Update files
    update_pyproject(project_root, new_version)
    update_backend_main(project_root, new_version)
    update_frontend_package(project_root, new_version)
    update_frontend_files_version(project_root, new_version)

    # Commit and tag
    auto_push = args.push
    if not auto_push:
        push_choice = (
            input("\nDo you want to automatically push to origin? (y/N): ").strip().lower()
        )
        auto_push = push_choice in ("y", "yes")

    git_operations(project_root, new_version, auto_push)


if __name__ == "__main__":
    main()
