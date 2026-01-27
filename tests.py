
import unittest
import os
import shutil
import tempfile
import subprocess
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base, Repository, Settings, ErrorLog
from src.main import process_repo, handle_error
from src.services.docker_service import DockerService

# In-memory DB for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class TestAppManager(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=engine)

    @patch("src.main.GitService")
    @patch("src.main.docker_service")
    @patch("src.main.JulesService")
    def test_flow_error_reporting(self, mock_jules, mock_docker, mock_git):
        # Setup Mocks
        mock_git.clone_repo.return_value = (True, "Cloned")
        mock_git.pull_repo.return_value = (True, "Updated")

        # Simulate Build Failure
        mock_docker.build_and_run.return_value = (False, "Build Failed: Syntax Error")

        # Simulate Jules API success
        mock_jules.report_error.return_value = (True, "sessions/12345")

        # Create Repo
        repo = Repository(url="https://github.com/test/repo.git", status="pending")
        self.db.add(repo)
        self.db.commit()

        # Run Process
        process_repo(repo, self.db, "fake-api-key")

        # Verification 1: Status should be error
        updated_repo = self.db.query(Repository).filter_by(id=repo.id).first()
        self.assertEqual(updated_repo.status, "error")

        # Verification 2: Error should be logged in DB
        error_log = self.db.query(ErrorLog).first()
        self.assertIsNotNone(error_log)
        self.assertIn("Build Failed", error_log.error_message)

        # Verification 3: Jules Service should be called
        mock_jules.report_error.assert_called_once()
        args, _ = mock_jules.report_error.call_args
        self.assertEqual(args[0], "fake-api-key") # API Key
        self.assertIn("Build Failed", args[3]) # Error message

    @patch("src.main.GitService")
    @patch("src.main.docker_service")
    @patch("src.main.JulesService")
    def test_duplicate_error_suppression(self, mock_jules, mock_docker, mock_git):
        # Setup Mocks
        mock_git.clone_repo.return_value = (True, "Cloned")
        mock_docker.build_and_run.return_value = (False, "Same Error")
        mock_jules.report_error.return_value = (True, "sessions/123")

        # Create Repo with existing error hash
        import hashlib
        error_msg = "Build/Run Error:\nSame Error"
        error_hash = hashlib.md5(error_msg.encode("utf-8")).hexdigest()

        repo = Repository(
            url="https://github.com/test/repo.git",
            status="error",
            last_error_hash=error_hash # Pre-existing error
        )
        self.db.add(repo)
        self.db.commit()

        # Run Process
        process_repo(repo, self.db, "fake-api-key")

        # Verification: Jules Service should NOT be called again
        mock_jules.report_error.assert_not_called()

    @patch("src.main.GitService")
    @patch("src.main.docker_service")
    def test_process_repo_arguments(self, mock_docker, mock_git):
        # Setup Mocks
        mock_git.clone_repo.return_value = (True, "Cloned")
        mock_docker.build_and_run.return_value = (True, "Success")
        mock_docker.get_logs.return_value = "Everything OK"

        # Create Repo
        repo = Repository(url="https://github.com/test/repo.git", status="pending")
        self.db.add(repo)
        self.db.commit()

        # Run Process
        process_repo(repo, self.db, "fake-api-key")

        # Verify build_and_run called with timeout and log file
        mock_docker.build_and_run.assert_called_once()
        kwargs = mock_docker.build_and_run.call_args[1]
        self.assertEqual(kwargs['timeout'], 300)
        self.assertIn('logs', kwargs['log_filepath'])
        self.assertTrue(kwargs['log_filepath'].endswith(f"{repo.id}.log"))

class TestProcessLogs(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()
        self.temp_dir = tempfile.mkdtemp()
        self.logs_dir = os.path.join(self.temp_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=engine)
        shutil.rmtree(self.temp_dir)

    @patch("src.main.GitService")
    @patch("src.main.docker_service")
    @patch("src.main.JulesService")
    def test_logs_creation(self, mock_jules, mock_docker, mock_git):
        # Setup
        mock_git.clone_repo.return_value = (True, "Cloned Successfully")
        mock_docker.build_and_run.return_value = (True, "Built Successfully")
        mock_docker.get_logs.return_value = "Container Logs"

        repo = Repository(url="https://github.com/test/repo-logs.git", status="pending")
        self.db.add(repo)
        self.db.commit()

        # We need to patch LOGS_DIR in src.main to point to self.logs_dir
        with patch("src.main.LOGS_DIR", self.logs_dir):
            process_repo(repo, self.db, "api-key")

        # Verify File Exists
        log_file = os.path.join(self.logs_dir, f"{repo.id}.log")
        self.assertTrue(os.path.exists(log_file), "Log file was not created")

        # Verify Content
        with open(log_file, "r") as f:
            content = f.read()

        self.assertIn("--- Starting Job", content)
        self.assertIn("Cloning from https://github.com/test/repo-logs.git", content)
        self.assertIn("Clone Result: True - Cloned Successfully", content)
        self.assertIn("Starting Docker build/run sequence", content)

        # Verify Docker Service was called with correct log file path
        mock_docker.build_and_run.assert_called_once()
        call_args = mock_docker.build_and_run.call_args[1]
        self.assertEqual(call_args['log_filepath'], log_file)

    @patch("src.main.GitService")
    @patch("src.main.docker_service")
    @patch("src.main.JulesService")
    def test_logs_on_git_failure(self, mock_jules, mock_docker, mock_git):
        # Setup Git Failure
        mock_git.clone_repo.return_value = (False, "Authentication Failed")
        mock_jules.report_error.return_value = (True, "Reported")

        repo = Repository(url="https://github.com/test/repo-fail.git", status="pending")
        self.db.add(repo)
        self.db.commit()

        with patch("src.main.LOGS_DIR", self.logs_dir):
            process_repo(repo, self.db, "api-key")

        log_file = os.path.join(self.logs_dir, f"{repo.id}.log")
        self.assertTrue(os.path.exists(log_file))

        with open(log_file, "r") as f:
            content = f.read()

        self.assertIn("Job failed during Git Clone", content)
        self.assertIn("Authentication Failed", content)

        # Docker should NOT be called
        mock_docker.build_and_run.assert_not_called()

class TestDockerService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    @patch("src.services.docker_service.docker.from_env")
    @patch("src.services.docker_service.time.sleep")
    @patch("src.services.docker_service.subprocess.run")
    def test_race_condition_fix(self, mock_subprocess, mock_sleep, mock_docker_env):
        # Setup
        mock_client = MagicMock()
        mock_docker_env.return_value = mock_client

        # Simulate existing container
        mock_container = MagicMock()
        mock_client.containers.get.return_value = mock_container

        # Mock run command success
        mock_subprocess.return_value.returncode = 0

        service = DockerService()

        # Call _handle_dockerfile directly
        service._handle_dockerfile(
            path=self.temp_dir,
            repo_name="test-repo",
            ports=None, volumes=None, env=None,
            container_name="test-container",
            log_filepath=None,
            timeout=300
        )

        # Verify stop and remove called
        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()

        # Verify sleep is NOT called (removed feature)
        mock_sleep.assert_not_called()

    @patch("src.services.docker_service.docker.from_env")
    @patch("src.services.docker_service.time.sleep")
    @patch("src.services.docker_service.subprocess.run")
    def test_retry_logic_compose(self, mock_subprocess, mock_sleep, mock_docker_env):
        service = DockerService()
        log_filepath = os.path.join(self.temp_dir, "test.log")

        call_counter = {"count": 0}
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "build" in cmd:
                return MagicMock(returncode=0)
            if "up" in cmd:
                call_counter["count"] += 1
                if call_counter["count"] == 1:
                    # First UP fails, simulate writing error to log file (since we passed log_filepath)
                    f = kwargs.get('stdout')
                    if f:
                        f.write("Bind for 0.0.0.0:80 failed: port is already allocated\n")
                        f.flush()
                    raise subprocess.CalledProcessError(1, cmd)
                else:
                    # Retry succeeds
                    return MagicMock(returncode=0)
            return MagicMock(returncode=0)

        mock_subprocess.side_effect = side_effect

        service._handle_compose(
            path=self.temp_dir,
            compose_file="docker-compose.yml",
            log_filepath=log_filepath,
            timeout=300
        )

        # Verify sleep called once (5s) for the retry
        mock_sleep.assert_called_with(5)

        # Verify call count: 1 build + 2 ups = 3 calls
        self.assertEqual(mock_subprocess.call_count, 3)

    @patch("src.services.docker_service.docker.from_env")
    @patch("src.services.docker_service.time.sleep")
    @patch("src.services.docker_service.subprocess.run")
    def test_retry_logic_dockerfile(self, mock_subprocess, mock_sleep, mock_docker_env):
        # Setup
        import docker
        mock_client = MagicMock()
        mock_docker_env.return_value = mock_client

        # Build mock (subprocess) needs to succeed
        mock_subprocess.return_value.returncode = 0

        # Run mock needs to fail then succeed
        # Mock client.containers.run

        # Error instance with status code for is_client_error() check
        response = MagicMock()
        response.status_code = 500
        # The explanation is what typically appears in the string representation for 500 errors
        api_error = docker.errors.APIError(
            "Bind error",
            response=response,
            explanation="Bind for 0.0.0.0:80 failed: port is already allocated"
        )

        # Side effect: Raise, then Return Container
        mock_client.containers.run.side_effect = [api_error, MagicMock()]

        # Setup get return value for cleanup
        mock_failed_container = MagicMock()
        mock_client.containers.get.return_value = mock_failed_container

        service = DockerService()

        service._handle_dockerfile(
            path=self.temp_dir,
            repo_name="test",
            ports={}, volumes={}, env={},
            container_name="test",
            log_filepath=None,
            timeout=300
        )

        # Verify sleep called once
        mock_sleep.assert_called_with(5)

        # Verify run called twice
        self.assertEqual(mock_client.containers.run.call_count, 2)

        # Verify cleanup called on failed attempt
        mock_failed_container.remove.assert_called_with(force=True)

if __name__ == "__main__":
    unittest.main()
