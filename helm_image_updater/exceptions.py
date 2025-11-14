"""Custom exceptions for Helm Image Updater."""


class AutoMergeError(Exception):
    """Raised when automatic PR merge fails or times out."""

    def __init__(self, message: str, pr_url: str = None):
        self.pr_url = pr_url
        super().__init__(message)
