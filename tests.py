
import unittest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base, Repository, Settings, ErrorLog
from src.main import process_repo, handle_error

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

if __name__ == "__main__":
    unittest.main()
