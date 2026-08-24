"""Generate static dashboard JSON and push to both repos."""

import subprocess
import sys


def run(cmd: list[str], **kwargs):
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def git_commit_push(repo_dir: str, paths: list[str], message: str):
    """Stage, commit, push. Skips gracefully if nothing changed."""
    run(["git", "-C", repo_dir, "add", *paths])
    result = subprocess.run(
        ["git", "-C", repo_dir, "commit", "-m", message],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        if "nothing to commit" in result.stdout:
            print("  (nothing to commit, skipping push)")
            return
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)
    print(result.stdout)
    run(["git", "-C", repo_dir, "push"])


def main():
    print("=" * 50)
    print("Generating and Publishing static Naukri Dashboard")
    print("=" * 50)

    run([sys.executable, "generate_dashboard.py"])

    # Naukri-Automation repo (docs/)
    print("\n--- Naukri-Automation repo ---")
    git_commit_push(".", ["docs"], "Update static dashboard data [automated]")

    # Parent repo (dashboard data & code)
    print("\n--- Parent repo ---")
    git_commit_push(
        "..",
        ["dashboard/public/data", "dashboard/src", "dashboard/backend"],
        "Update static dashboard data and code [automated]",
    )

    print("\nDashboard successfully published!")


if __name__ == "__main__":
    main()
