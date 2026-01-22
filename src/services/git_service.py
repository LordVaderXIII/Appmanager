import subprocess
import os
import shutil

class GitService:
    @staticmethod
    def _insert_auth(url: str, username: str = None, token: str = None) -> str:
        """Inserts authentication credentials into the URL if provided."""
        if not username or not token:
            return url

        # Strip https://
        clean_url = url.replace("https://", "").replace("http://", "")
        return f"https://{username}:{token}@{clean_url}"

    @staticmethod
    def clone_repo(url: str, destination_path: str, username: str = None, token: str = None):
        """Clones a repository to the destination path, optionally using credentials."""
        if os.path.exists(destination_path):
            shutil.rmtree(destination_path)

        auth_url = GitService._insert_auth(url, username, token)

        try:
            # We don't want to log the token, so we capture output but be careful with it
            subprocess.run(
                ["git", "clone", auth_url, destination_path],
                check=True,
                capture_output=True,
                text=True
            )
            return True, "Cloned successfully"
        except subprocess.CalledProcessError as e:
            # Redact token from error message if present
            safe_error = e.stderr.replace(token, "***") if token else e.stderr
            return False, f"Clone failed: {safe_error}"

    @staticmethod
    def pull_repo(local_path: str, url: str = None, username: str = None, token: str = None):
        """Pulls updates for an existing repository."""
        if not os.path.exists(local_path):
            return False, "Repository path does not exist"

        try:
            # Update remote URL to ensure auth state matches current settings
            # We do this every time if we have the URL, to handle added/removed/changed tokens
            if url:
                auth_url = GitService._insert_auth(url, username, token)
                subprocess.run(
                    ["git", "remote", "set-url", "origin", auth_url],
                    cwd=local_path,
                    check=True,
                    capture_output=True,
                    text=True
                )

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
            safe_error = e.stderr
            if token:
                 safe_error = safe_error.replace(token, "***")
            return False, f"Pull failed: {safe_error}"
