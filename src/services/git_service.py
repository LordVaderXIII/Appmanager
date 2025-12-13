import subprocess
import os
import shutil

class GitService:
    @staticmethod
    def clone_repo(url: str, destination_path: str):
        """Clones a public repository to the destination path."""
        if os.path.exists(destination_path):
            shutil.rmtree(destination_path)

        try:
            subprocess.run(
                ["git", "clone", url, destination_path],
                check=True,
                capture_output=True,
                text=True
            )
            return True, "Cloned successfully"
        except subprocess.CalledProcessError as e:
            return False, f"Clone failed: {e.stderr}"

    @staticmethod
    def pull_repo(local_path: str):
        """Pulls updates for an existing repository."""
        if not os.path.exists(local_path):
            return False, "Repository path does not exist"

        try:
            # Check if there are updates
            # Fetch origin
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=local_path,
                check=True,
                capture_output=True,
                text=True
            )

            # Check status
            status_output = subprocess.run(
                ["git", "status", "-uno"],
                cwd=local_path,
                capture_output=True,
                text=True
            ).stdout

            if "Your branch is up to date" in status_output:
                return False, "No updates"

            # Pull
            subprocess.run(
                ["git", "pull"],
                cwd=local_path,
                check=True,
                capture_output=True,
                text=True
            )
            return True, "Updated successfully"
        except subprocess.CalledProcessError as e:
            return False, f"Pull failed: {e.stderr}"
