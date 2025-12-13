import subprocess
import os
import docker

class DockerService:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def get_compose_file(self, path: str):
        """Checks for docker-compose.yml or docker-compose.yaml"""
        if os.path.exists(os.path.join(path, "docker-compose.yml")):
            return "docker-compose.yml"
        if os.path.exists(os.path.join(path, "docker-compose.yaml")):
            return "docker-compose.yaml"
        return None

    def build_and_run(self, repo_path: str, repo_name: str):
        """
        Builds and runs the project.
        Returns: (success: bool, logs: str)
        """
        compose_file = self.get_compose_file(repo_path)

        if compose_file:
            return self._handle_compose(repo_path, compose_file)
        elif os.path.exists(os.path.join(repo_path, "Dockerfile")):
            return self._handle_dockerfile(repo_path, repo_name)
        else:
            return False, "No Dockerfile or docker-compose.yml found."

    def _handle_compose(self, path: str, compose_file: str):
        try:
            # Build
            build_cmd = ["docker", "compose", "-f", compose_file, "build"]
            build_res = subprocess.run(
                build_cmd, cwd=path, capture_output=True, text=True
            )
            if build_res.returncode != 0:
                return False, f"Build Failed:\n{build_res.stderr}\n{build_res.stdout}"

            # Up
            up_cmd = ["docker", "compose", "-f", compose_file, "up", "-d"]
            up_res = subprocess.run(
                up_cmd, cwd=path, capture_output=True, text=True
            )
            if up_res.returncode != 0:
                return False, f"Start Failed:\n{up_res.stderr}\n{up_res.stdout}"

            return True, "Compose Up Successful"
        except Exception as e:
            return False, str(e)

    def _handle_dockerfile(self, path: str, tag_name: str):
        # Sanitize tag name (lowercase, no spaces, restricted chars)
        tag = "".join(c if c.isalnum() else "_" for c in tag_name).lower()

        try:
            # Build
            build_cmd = ["docker", "build", "-t", tag, "."]
            build_res = subprocess.run(
                build_cmd, cwd=path, capture_output=True, text=True
            )
            if build_res.returncode != 0:
                return False, f"Build Failed:\n{build_res.stderr}\n{build_res.stdout}"

            # Stop existing container if running
            try:
                existing = self.client.containers.get(tag)
                existing.stop()
                existing.remove()
            except docker.errors.NotFound:
                pass

            # Run
            # We run detached, mapped to host network or default bridge?
            # User said "mapped to this container", but actually said "apps logs need to be mapped"
            # Best effort: Run detached.
            self.client.containers.run(tag, detach=True, name=tag)

            return True, "Container Started"
        except Exception as e:
            return False, str(e)

    def get_logs(self, repo_path: str, repo_name: str):
        """Fetch logs from running containers associated with the repo."""
        logs = ""
        compose_file = self.get_compose_file(repo_path)

        if compose_file:
             try:
                 # docker compose logs returns logs for all services in the compose
                 res = subprocess.run(
                     ["docker", "compose", "-f", compose_file, "logs", "--no-color", "--tail", "100"],
                     cwd=repo_path, capture_output=True, text=True
                 )
                 logs = res.stdout + res.stderr
             except Exception as e:
                 logs = f"Error fetching logs: {e}"
        else:
             tag = "".join(c if c.isalnum() else "_" for c in repo_name).lower()
             try:
                 container = self.client.containers.get(tag)
                 logs = container.logs(tail=100).decode("utf-8")
             except docker.errors.NotFound:
                 logs = "Container not found."
             except Exception as e:
                 logs = f"Error fetching logs: {e}"

        return logs
