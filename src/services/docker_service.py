import subprocess
import os
import docker
import socket
import logging
import time
from typing import List, Dict, Any, Optional

logger = logging.getLogger("DockerService")

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

    def list_containers(self, filter_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Lists running containers.
        Optionally exclude containers with names in filter_names.
        """
        try:
            containers = self.client.containers.list()
            result = []
            for c in containers:
                name = c.name
                if filter_names and name in filter_names:
                    continue
                result.append({
                    "id": c.id,
                    "name": name,
                    "image": c.image.tags[0] if c.image.tags else c.image.id,
                    "status": c.status
                })
            return result
        except Exception as e:
            logger.error(f"Error listing containers: {e}")
            return []

    def inspect_container(self, container_id: str) -> Optional[Dict[str, Any]]:
        """
        Inspects a container and returns relevant config for adoption.
        """
        try:
            container = self.client.containers.get(container_id)
            attrs = container.attrs

            # Extract Ports
            # NetworkSettings.Ports is like {'80/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '8080'}]}
            port_bindings = {}
            if attrs.get("NetworkSettings", {}).get("Ports"):
                for internal, external_list in attrs["NetworkSettings"]["Ports"].items():
                    if external_list:
                        # Take the first binding
                        port_bindings[internal] = int(external_list[0]["HostPort"])

            # Extract Mounts
            # Mounts is a list of dicts
            volume_bindings = {}
            for mount in attrs.get("Mounts", []):
                if mount["Type"] == "bind":
                    # Source is host path, Destination is container path
                    volume_bindings[mount["Source"]] = {
                        "bind": mount["Destination"],
                        "mode": "rw" # Defaulting to rw, can check mount['Mode'] if needed
                    }

            # Extract Env
            # Config.Env is ["KEY=VAL", ...]
            env_vars = {}
            for env_str in attrs.get("Config", {}).get("Env", []):
                if "=" in env_str:
                    key, val = env_str.split("=", 1)
                    env_vars[key] = val

            return {
                "name": container.name,
                "ports": port_bindings,
                "volumes": volume_bindings,
                "env": env_vars,
                "image": attrs.get("Config", {}).get("Image")
            }

        except Exception as e:
            logger.error(f"Error inspecting container {container_id}: {e}")
            return None

    def find_available_port(self, start=8000, end=9000) -> int:
        """Finds a free port in range."""
        for port in range(start, end):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(('localhost', port)) != 0:
                    return port
        return 0

    def _run_cmd(self, cmd: List[str], cwd: str, log_filepath: Optional[str], timeout: int) -> tuple[bool, str]:
        """Helper to run subprocess commands with logging and timeout."""
        f_handle = None
        try:
            if log_filepath:
                # Open in append mode so we can chain commands (e.g. build then run)
                # But caller should handle truncation if it's the start of a sequence.
                # Here we assume the caller manages the file lifecycle or we append.
                # Actually, usually 'w' for the first command, 'a' for subsequent?
                # For simplicity, let's use 'a' and assume caller truncated if needed.
                # BUT, if we want to clear previous build logs, we should do it at the start of build_and_run.
                f_handle = open(log_filepath, "a")
                f_handle.write(f"\n--- Executing: {' '.join(cmd)} ---\n")
                f_handle.flush()

                subprocess.run(
                    cmd, cwd=cwd, stdout=f_handle, stderr=subprocess.STDOUT, timeout=timeout, check=True
                )
                return True, "Success"
            else:
                res = subprocess.run(
                    cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
                )
                if res.returncode != 0:
                    return False, f"Failed:\n{res.stderr}\n{res.stdout}"
                return True, "Success"

        except subprocess.TimeoutExpired:
            msg = f"Process timed out after {timeout} seconds."
            if f_handle:
                f_handle.write(f"\n[ERROR] {msg}\n")
            return False, msg
        except subprocess.CalledProcessError as e:
            msg = f"Process failed with exit code {e.returncode}."
            if f_handle:
                f_handle.write(f"\n[ERROR] {msg}\n")
            return False, msg
        except Exception as e:
            msg = f"Unexpected error: {str(e)}"
            if f_handle:
                 f_handle.write(f"\n[ERROR] {msg}\n")
            return False, msg
        finally:
            if f_handle:
                f_handle.close()

    def build_and_run(self, repo_path: str, repo_name: str,
                      ports: Optional[Dict] = None,
                      volumes: Optional[Dict] = None,
                      env: Optional[Dict] = None,
                      container_name: Optional[str] = None,
                      log_filepath: Optional[str] = None,
                      timeout: int = 300):
        """
        Builds and runs the project.
        Returns: (success: bool, logs: str)
        """
        # Append to log file (initialized by orchestrator)
        if log_filepath:
            try:
                with open(log_filepath, "a") as f:
                    f.write(f"\nStarting Docker build/run for {repo_name}...\n")
            except Exception as e:
                logger.error(f"Could not write to log file {log_filepath}: {e}")

        compose_file = self.get_compose_file(repo_path)

        if compose_file:
            return self._handle_compose(repo_path, compose_file, log_filepath, timeout)
        elif os.path.exists(os.path.join(repo_path, "Dockerfile")):
            return self._handle_dockerfile(repo_path, repo_name, ports, volumes, env, container_name, log_filepath, timeout)
        else:
            return False, "No Dockerfile or docker-compose.yml found."

    def _handle_compose(self, path: str, compose_file: str, log_filepath: str, timeout: int):
        # Build
        success, msg = self._run_cmd(
            ["docker", "compose", "-f", compose_file, "build"],
            cwd=path, log_filepath=log_filepath, timeout=timeout
        )
        if not success:
            return False, msg

        # Up
        success, msg = self._run_cmd(
            ["docker", "compose", "-f", compose_file, "up", "-d"],
            cwd=path, log_filepath=log_filepath, timeout=timeout
        )
        if not success:
            return False, msg

        return True, "Compose Up Successful"

    def _handle_dockerfile(self, path: str, repo_name: str,
                           ports: Optional[Dict],
                           volumes: Optional[Dict],
                           env: Optional[Dict],
                           container_name: Optional[str],
                           log_filepath: str,
                           timeout: int):
        # Default tag from repo name if no custom container name
        tag_name = container_name if container_name else repo_name
        # Sanitize tag name (lowercase, no spaces, restricted chars)
        # Allow alphanumeric, hyphens, and dots. Replace others with underscore.
        tag = "".join(c if c.isalnum() or c in ['-', '.'] else "_" for c in tag_name).lower()

        # Build
        success, msg = self._run_cmd(
            ["docker", "build", "-t", tag, "."],
            cwd=path, log_filepath=log_filepath, timeout=timeout
        )
        if not success:
            return False, msg

        # Stop existing container if running
        try:
            if log_filepath:
                with open(log_filepath, "a") as f:
                    f.write(f"\nStopping existing container {tag}...\n")

            existing = self.client.containers.get(tag)
            existing.stop()
            # Wait for port release?
            existing.remove()
            if log_filepath:
                with open(log_filepath, "a") as f:
                    f.write("\nWaiting 2s for port release...\n")
            time.sleep(2)
        except docker.errors.NotFound:
            pass
        except Exception as e:
            logger.warning(f"Could not stop/remove existing container {tag}: {e}")
            if log_filepath:
                with open(log_filepath, "a") as f:
                     f.write(f"\nWarning: Could not stop/remove existing container: {e}\n")

        # Run with Config
        try:
            if log_filepath:
                with open(log_filepath, "a") as f:
                    f.write(f"\nStarting container {tag}...\n")

            run_kwargs = {
                "detach": True,
                "name": tag,
                "restart_policy": {"Name": "unless-stopped"}
            }

            if ports:
                run_kwargs["ports"] = ports

            if volumes:
                run_kwargs["volumes"] = volumes

            if env:
                run_kwargs["environment"] = env

            self.client.containers.run(tag, **run_kwargs)

            if log_filepath:
                with open(log_filepath, "a") as f:
                    f.write(f"\nContainer {tag} started successfully.\n")

            return True, "Container Started"
        except Exception as e:
            msg = f"Run Error: {str(e)}"
            if log_filepath:
                with open(log_filepath, "a") as f:
                    f.write(f"\n[ERROR] {msg}\n")
            return False, msg

    def get_logs(self, repo_path: str, repo_name: str, container_name: str = None):
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
             # Use custom container name if provided, else fallback to repo name derivation
             tag_name = container_name if container_name else repo_name
             tag = "".join(c if c.isalnum() or c in ['-', '.'] else "_" for c in tag_name).lower()

             try:
                 container = self.client.containers.get(tag)
                 logs = container.logs(tail=100).decode("utf-8")
             except docker.errors.NotFound:
                 logs = "Container not found."
             except Exception as e:
                 logs = f"Error fetching logs: {e}"

        return logs
