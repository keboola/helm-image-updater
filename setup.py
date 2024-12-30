"""Setup configuration for helm-image-updater package.

This module configures the package for distribution, including dependencies,
entry points, and metadata. It reads requirements from requirements.txt if available,
otherwise uses a default set of requirements.

Example:
    To install the package:
        $ pip install .
    
    To build the package:
        $ python setup.py sdist bdist_wheel

Attributes:
    requirements_file (Path): Path to requirements.txt file
    requirements (list): List of package dependencies
"""

from pathlib import Path
from setuptools import setup, find_packages

requirements_file = Path("requirements.txt")
if requirements_file.exists():
    with open(requirements_file, encoding="utf-8") as f:
        requirements = f.read().splitlines()
else:
    # Default requirements if file is not found
    requirements = [
        "PyYAML>=6.0",
        "GitPython>=3.1.0",
        "PyGithub>=2.1.1",
        "dpath>=2.1.0",
    ]

setup(
    name="helm_image_updater",
    version="0.1.0",
    packages=find_packages(),
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "helm-image-updater=helm_image_updater.cli:main",
        ],
    },
    python_requires=">=3.13",
    description="Tool for updating Helm chart image tags across different stacks",
    author="Keboola",
    author_email="michal.kozak@keboola.com",
    url="https://github.com/keboola/helm-image-updater",
)
