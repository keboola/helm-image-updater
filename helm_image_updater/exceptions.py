"""
Custom Exceptions for Helm Image Updater

This module defines custom exceptions used throughout the application to handle
specific error cases in a structured way.

Exceptions:
    ImageUpdaterError: Base exception for all image updater errors
    TagFileError: Raised for issues with tag.yaml files
    GitOperationError: Raised for Git operation failures
"""


class ImageUpdaterError(Exception):
    """Base exception for image updater errors."""


class TagFileError(ImageUpdaterError):
    """Raised when there are issues with tag.yaml files."""


class GitOperationError(ImageUpdaterError):
    """Raised when git operations fail."""
