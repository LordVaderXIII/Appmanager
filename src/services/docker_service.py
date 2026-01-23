import subprocess
import os
import docker
import socket
import logging
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

    def build_and_run(self, repo_path: str, repo_name: str,
                      ports: Optional[Dict] = None,
                      volumes: Optional[Dict] = None,
                      env: Optional[Dict] = None,
                      container_name: Optional[str] = None):
        """
        Builds and runs the project.
        Returns: (success: bool, logs: str)
        """
        compose_file = self.get_compose_file(repo_path)

        if compose_file:
            return self._handle_compose(repo_path, compose_file)
        elif os.path.exists(os.path.join(repo_path, "Dockerfile")):
            return self._handle_dockerfile(repo_path, repo_name, ports, volumes, env, container_name)
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

    def _handle_dockerfile(self, path: str, repo_name: str,
                           ports: Optional[Dict],
                           volumes: Optional[Dict],
                           env: Optional[Dict],
                           container_name: Optional[str]):
        # Default tag from repo name if no custom container name
        tag_name = container_name if container_name else repo_name
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
                # Wait for port release?
                # Script had sleep 2. We can try wait() but remove() is sync.
                existing.remove()
            except docker.errors.NotFound:
                pass
            except Exception as e:
                logger.warning(f"Could not stop/remove existing container {tag}: {e}")

            # Run with Config
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

            return True, "Container Started"
        except Exception as e:
            return False, str(e)

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
             tag = "".join(c if c.isalnum() else "_" for c in tag_name).lower()

             try:
                 container = self.client.containers.get(tag)
                 logs = container.logs(tail=100).decode("utf-8")
             except docker.errors.NotFound:
                 logs = "Container not found."
             except Exception as e:
                 logs = f"Error fetching logs: {e}"

        return logs
