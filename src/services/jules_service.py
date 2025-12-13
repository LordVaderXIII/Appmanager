import requests
import json
import logging

logger = logging.getLogger(__name__)

class JulesService:
    BASE_URL = "https://jules.googleapis.com/v1alpha"

    @staticmethod
    def _get_headers(api_key: str):
        return {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key
        }

    @classmethod
    def report_error(cls, api_key: str, repo_url: str, repo_name: str, error_log: str):
        """
        Reports an error to Jules API by creating a session.
        """
        if not api_key:
            logger.error("No Jules API Key provided.")
            return False, "No API Key"

        # 1. Find the source name for the repo
        source_name = cls._find_source(api_key, repo_name)
        if not source_name:
            # Fallback: Try to deduce source name if standard format or fail
            # Typically source name is "sources/github/{owner}/{repo}"
            # We will attempt to construct it if search fails or just warn.
            # However, doc says "Before using a source... must first install...".
            # If not in list, we likely can't use it.
            logger.warning(f"Source for {repo_name} not found in Jules API.")
            # Attempting to construct it blindly might work if the list was paginated and we missed it,
            # but strictly adhering to "find it first" is safer.
            # Let's try to construct it:
            # repo_name is likely "owner/repo" from the DB.
            source_name = f"sources/github/{repo_name}"

        # 2. Create Session
        prompt = f"I encountered an error running the app. Here is the error log:\n\n{error_log}\n\nPlease fix it."

        payload = {
            "prompt": prompt,
            "sourceContext": {
                "source": source_name,
                "githubRepoContext": {
                    "startingBranch": "main" # Assumption
                }
            },
            "automationMode": "AUTO_CREATE_PR", # Let Jules try to fix it directly
            "title": f"Fix build/run error for {repo_name}"
        }

        try:
            response = requests.post(
                f"{cls.BASE_URL}/sessions",
                headers=cls._get_headers(api_key),
                json=payload
            )
            response.raise_for_status()
            session_data = response.json()
            logger.info(f"Created Jules session: {session_data.get('name')}")
            return True, session_data.get("name")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to create Jules session: {e}")
            if e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return False, str(e)

    @classmethod
    def _find_source(cls, api_key: str, repo_name: str):
        """
        Iterates through sources to find the matching one.
        repo_name is expected to be 'owner/repo'.
        """
        url = f"{cls.BASE_URL}/sources"
        next_page_token = None

        while True:
            params = {}
            if next_page_token:
                params["pageToken"] = next_page_token

            try:
                response = requests.get(url, headers=cls._get_headers(api_key), params=params)
                response.raise_for_status()
                data = response.json()

                for source in data.get("sources", []):
                    gh_repo = source.get("githubRepo", {})
                    # Construct "owner/repo"
                    current_repo_name = f"{gh_repo.get('owner')}/{gh_repo.get('repo')}"
                    if current_repo_name.lower() == repo_name.lower():
                        return source.get("name")

                next_page_token = data.get("nextPageToken")
                if not next_page_token:
                    break
            except Exception as e:
                logger.error(f"Error fetching sources: {e}")
                break

        return None
