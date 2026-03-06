"""Unit tests for IOLayer auto-merge and auto-approve functionality."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from github.GithubException import GithubException

from helm_image_updater.io_layer import IOLayer
from helm_image_updater.exceptions import AutoMergeError, AutoApproveError


class TestAutoMerge:
    """Test auto-merge functionality in IOLayer."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock Git repository."""
        repo = Mock()
        repo.git = Mock()
        return repo

    @pytest.fixture
    def mock_github_repo(self):
        """Create a mock GitHub repository."""
        return Mock()

    @pytest.fixture
    def mock_approve_github_repo(self):
        """Create a mock GitHub repository for approval."""
        return Mock()

    @pytest.fixture
    def io_layer(self, mock_repo, mock_github_repo, mock_approve_github_repo):
        """Create an IOLayer instance with mocked dependencies."""
        return IOLayer(mock_repo, mock_github_repo, dry_run=False, approve_github_repo=mock_approve_github_repo)

    def test_auto_merge_timeout_raises_exception(self, io_layer):
        """Test that auto-merge raises AutoMergeError when PR mergeable status remains None."""
        # Create a mock PR that never becomes mergeable
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/123"
        mock_pr.mergeable = None  # Stays None even after update()
        mock_pr.update = Mock()  # update() doesn't change mergeable status

        # Should raise AutoMergeError after 5 retries
        with pytest.raises(AutoMergeError) as exc_info:
            io_layer._attempt_auto_merge(mock_pr, max_retries=5, retry_delay=0)

        # Verify exception details
        assert "Failed to auto-merge PR after 5 attempts" in str(exc_info.value)
        assert "PR mergeability could not be determined" in str(exc_info.value)
        assert exc_info.value.pr_url == "https://github.com/test/repo/pull/123"

        # Verify update() was called 5 times (once per retry)
        assert mock_pr.update.call_count == 5

    def test_auto_merge_conflict_raises_exception(self, io_layer):
        """Test that auto-merge raises AutoMergeError when PR has conflicts."""
        # Create a mock PR with conflicts
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/456"
        mock_pr.mergeable = False  # Has conflicts
        mock_pr.update = Mock()

        # Should raise AutoMergeError immediately (no retries for conflicts)
        with pytest.raises(AutoMergeError) as exc_info:
            io_layer._attempt_auto_merge(mock_pr, max_retries=5, retry_delay=0)

        # Verify exception details
        assert "PR is not mergeable due to conflicts" in str(exc_info.value)
        assert exc_info.value.pr_url == "https://github.com/test/repo/pull/456"

        # Verify update() was called only once
        assert mock_pr.update.call_count == 1

    def test_auto_merge_success(self, io_layer):
        """Test successful auto-merge when PR becomes mergeable."""
        # Create a mock PR that is mergeable
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/789"
        mock_pr.mergeable = True
        mock_pr.update = Mock()
        mock_pr.merge = Mock()

        # Should merge successfully without raising
        io_layer._attempt_auto_merge(mock_pr, max_retries=5, retry_delay=0)

        # Verify merge was called
        mock_pr.merge.assert_called_once()
        mock_pr.update.assert_called_once()

    def test_auto_merge_becomes_mergeable_after_retries(self, io_layer):
        """Test auto-merge succeeds when PR becomes mergeable after initial retries."""
        # Create a mock PR that becomes mergeable after 2 attempts
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/101"
        mock_pr.merge = Mock()

        # First 2 calls return None, then becomes True
        mock_pr.mergeable = None
        def update_side_effect():
            if mock_pr.update.call_count >= 2:
                mock_pr.mergeable = True
        mock_pr.update = Mock(side_effect=update_side_effect)

        # Should succeed after retries
        io_layer._attempt_auto_merge(mock_pr, max_retries=5, retry_delay=0)

        # Verify merge was called
        mock_pr.merge.assert_called_once()
        assert mock_pr.update.call_count == 2

    def test_auto_merge_github_exception_405_retries_then_fails(self, io_layer):
        """Test that 405 GithubException triggers retries and eventually raises AutoMergeError."""
        # Create a mock PR that always throws 405
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/202"
        mock_pr.mergeable = True
        mock_pr.update = Mock()

        # Mock merge to throw 405 exception
        exception_data = {"message": "Pull Request is not mergeable"}
        github_exception = GithubException(405, exception_data)
        mock_pr.merge = Mock(side_effect=github_exception)

        # Should raise AutoMergeError after retries
        with pytest.raises(AutoMergeError) as exc_info:
            io_layer._attempt_auto_merge(mock_pr, max_retries=3, retry_delay=0)

        # Verify exception details
        assert "Failed to merge PR after 3 attempts" in str(exc_info.value)
        assert exc_info.value.pr_url == "https://github.com/test/repo/pull/202"

        # Verify merge was attempted 3 times
        assert mock_pr.merge.call_count == 3

    def test_auto_merge_base_branch_modified_retries_then_succeeds(self, io_layer):
        """Test that 405 'Base branch was modified' exception triggers retries and succeeds."""
        # Create a mock PR
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/203"
        mock_pr.mergeable = True
        mock_pr.update = Mock()

        # Mock merge to throw 405 "Base branch was modified" twice, then succeed
        exception_data = {"message": "Base branch was modified. Review and try the merge again."}
        github_exception = GithubException(405, exception_data)
        mock_pr.merge = Mock(side_effect=[
            github_exception,
            github_exception,
            None  # Success on third attempt
        ])

        # Should succeed after retries
        io_layer._attempt_auto_merge(mock_pr, max_retries=5, retry_delay=0)

        # Verify merge was called 3 times
        assert mock_pr.merge.call_count == 3

    def test_auto_merge_other_github_exception_propagates(self, io_layer):
        """Test that non-405 GithubExceptions are propagated immediately."""
        # Create a mock PR
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/303"
        mock_pr.mergeable = True
        mock_pr.update = Mock()

        # Mock merge to throw 403 exception (permissions error)
        exception_data = {"message": "Forbidden"}
        github_exception = GithubException(403, exception_data)
        mock_pr.merge = Mock(side_effect=github_exception)

        # Should raise the original GithubException (not AutoMergeError)
        with pytest.raises(GithubException) as exc_info:
            io_layer._attempt_auto_merge(mock_pr, max_retries=5, retry_delay=0)

        # Verify it's the original exception
        assert exc_info.value.status == 403

        # Verify merge was only attempted once (no retries)
        assert mock_pr.merge.call_count == 1

    def test_create_pull_request_with_auto_merge_failure_propagates(self, io_layer, mock_github_repo):
        """Test that AutoMergeError from _attempt_auto_merge propagates through create_pull_request."""
        # Setup mock PR creation
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/404"
        mock_pr.mergeable = None  # Will timeout
        mock_pr.update = Mock()
        mock_github_repo.create_pull = Mock(return_value=mock_pr)

        # Mock push to not actually do anything
        with patch.object(io_layer, 'push_branch', return_value=True):
            # Should raise AutoMergeError when auto_merge=True
            with pytest.raises(AutoMergeError) as exc_info:
                io_layer.create_pull_request(
                    title="Test PR",
                    body="Test body",
                    branch_name="test-branch",
                    base_branch="main",
                    auto_merge=True
                )

            # Verify exception details
            assert "Failed to auto-merge PR after 10 attempts" in str(exc_info.value)
            assert exc_info.value.pr_url == "https://github.com/test/repo/pull/404"

        # Verify PR was created before merge failed
        mock_github_repo.create_pull.assert_called_once()


class TestAutoApprove:
    """Test auto-approve functionality in IOLayer."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock Git repository."""
        repo = Mock()
        repo.git = Mock()
        return repo

    @pytest.fixture
    def mock_github_repo(self):
        """Create a mock GitHub repository."""
        return Mock()

    @pytest.fixture
    def mock_approve_github_repo(self):
        """Create a mock GitHub repository for approval."""
        return Mock()

    def test_auto_approve_called_when_no_auto_merge_and_approve_repo_set(
        self, mock_repo, mock_github_repo, mock_approve_github_repo
    ):
        """Test that PR is auto-approved when auto_merge=False and approve_github_repo is set."""
        io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=False, approve_github_repo=mock_approve_github_repo)

        # Setup mock PR creation
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/1"
        mock_pr.number = 1
        mock_github_repo.create_pull.return_value = mock_pr

        # Setup mock approve PR
        mock_approve_pr = MagicMock()
        mock_approve_github_repo.get_pull.return_value = mock_approve_pr

        with patch.object(io_layer, 'push_branch', return_value=True):
            io_layer.create_pull_request(
                title="Test PR",
                body="Test body",
                branch_name="test-branch",
                base_branch="main",
                auto_merge=False
            )

        # Verify approval was requested
        mock_approve_github_repo.get_pull.assert_called_once_with(1)
        mock_approve_pr.create_review.assert_called_once_with(event="APPROVE")

    def test_auto_approve_not_called_when_auto_merge_true(
        self, mock_repo, mock_github_repo, mock_approve_github_repo
    ):
        """Test that PR is NOT auto-approved when auto_merge=True."""
        io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=False, approve_github_repo=mock_approve_github_repo)

        # Setup mock PR creation
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/2"
        mock_pr.number = 2
        mock_pr.mergeable = True
        mock_github_repo.create_pull.return_value = mock_pr

        with patch.object(io_layer, 'push_branch', return_value=True):
            io_layer.create_pull_request(
                title="Test PR",
                body="Test body",
                branch_name="test-branch",
                base_branch="main",
                auto_merge=True
            )

        # Verify approval was NOT requested
        mock_approve_github_repo.get_pull.assert_not_called()

    def test_auto_approve_failure_raises_error(
        self, mock_repo, mock_github_repo, mock_approve_github_repo
    ):
        """Test that approval failure raises AutoApproveError."""
        io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=False, approve_github_repo=mock_approve_github_repo)

        # Setup mock PR creation
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/4"
        mock_pr.number = 4
        mock_github_repo.create_pull.return_value = mock_pr

        # Setup approve to fail with non-404 error
        mock_approve_github_repo.get_pull.side_effect = GithubException(403, {"message": "Forbidden"})

        with patch.object(io_layer, 'push_branch', return_value=True):
            with pytest.raises(AutoApproveError):
                io_layer.create_pull_request(
                    title="Test PR",
                    body="Test body",
                    branch_name="test-branch",
                    base_branch="main",
                    auto_merge=False
                )

        mock_approve_github_repo.get_pull.assert_called_once_with(4)

    def test_auto_approve_retries_on_404_then_succeeds(
        self, mock_repo, mock_github_repo, mock_approve_github_repo
    ):
        """Test that 404 on first attempt is retried and succeeds on second."""
        io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=False, approve_github_repo=mock_approve_github_repo)

        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/5"
        mock_pr.number = 5

        mock_approve_pr = MagicMock()
        # First call raises 404, second call succeeds
        mock_approve_github_repo.get_pull.side_effect = [
            GithubException(404, {"message": "Not Found"}),
            mock_approve_pr,
        ]

        with patch('helm_image_updater.io_layer.sleep'):
            io_layer._auto_approve_pr(mock_pr)

        assert mock_approve_github_repo.get_pull.call_count == 2
        mock_approve_pr.create_review.assert_called_once_with(event="APPROVE")

    def test_auto_approve_retries_on_404_exhausted(
        self, mock_repo, mock_github_repo, mock_approve_github_repo
    ):
        """Test that repeated 404s exhaust retries and raise AutoApproveError."""
        io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=False, approve_github_repo=mock_approve_github_repo)

        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/6"
        mock_pr.number = 6

        mock_approve_github_repo.get_pull.side_effect = GithubException(404, {"message": "Not Found"})

        with patch('helm_image_updater.io_layer.sleep'):
            with pytest.raises(AutoApproveError, match="Failed to auto-approve PR after 5 attempt"):
                io_layer._auto_approve_pr(mock_pr)

        assert mock_approve_github_repo.get_pull.call_count == 5

    def test_auto_approve_non_404_fails_immediately(
        self, mock_repo, mock_github_repo, mock_approve_github_repo
    ):
        """Test that non-404 error (e.g. 403) raises AutoApproveError on first attempt without retry."""
        io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=False, approve_github_repo=mock_approve_github_repo)

        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/test/repo/pull/7"
        mock_pr.number = 7

        mock_approve_github_repo.get_pull.side_effect = GithubException(403, {"message": "Forbidden"})

        with pytest.raises(AutoApproveError, match="Failed to auto-approve PR after 1 attempt"):
            io_layer._auto_approve_pr(mock_pr)

        mock_approve_github_repo.get_pull.assert_called_once_with(7)

    def test_auto_approve_not_called_in_dry_run(
        self, mock_repo, mock_github_repo, mock_approve_github_repo
    ):
        """Test that auto-approve is not called during dry run."""
        io_layer = IOLayer(mock_repo, mock_github_repo, dry_run=True, approve_github_repo=mock_approve_github_repo)

        result = io_layer.create_pull_request(
            title="Test PR",
            body="Test body",
            branch_name="test-branch",
            base_branch="main",
            auto_merge=False
        )

        # Dry run returns None, no GitHub calls made
        assert result is None
        mock_approve_github_repo.get_pull.assert_not_called()
