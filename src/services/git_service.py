import subprocess
import os
import shutil
import requests
import logging

logger = logging.getLogger(__name__)

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

    @staticmethod
    def get_pr_status(pr_url: str, token: str = None) -> str:
        """
        Checks the status of a GitHub PR.
        Returns: 'merged', 'open', 'closed', or 'unknown'
        """
        if not pr_url or "github.com" not in pr_url:
            return "unknown"

        try:
            # Convert URL to API URL
            # https://github.com/owner/repo/pull/123 -> https://api.github.com/repos/owner/repo/pulls/123
            parts = pr_url.split("github.com/")[-1].split("/")
            if len(parts) < 4:
                return "unknown"

            owner, repo, _, pr_number = parts[:4]
            api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"

            headers = {"Accept": "application/vnd.github.v3+json"}
            if token:
                headers["Authorization"] = f"token {token}"

            resp = requests.get(api_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("merged"):
                    return "merged"
                state = data.get("state") # open, closed
                return state
            else:
                logger.warning(f"GitHub API Error: {resp.status_code} - {resp.text}")
                return "unknown"
        except Exception as e:
            logger.error(f"Error checking PR status: {e}")
            return "unknown"
