from pathlib import Path
from setuptools import setup, find_packages

# Look for requirements.txt in the helm_image_updater folder
requirements_file = Path("requirements.txt")
if requirements_file.exists():
    with open(requirements_file) as f:
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
    python_requires=">=3.8",
    description="Tool for updating Helm chart image tags across different stacks",
    author="Your Name",
    author_email="your.email@example.com",
    url="https://github.com/yourusername/helm-image-updater",
)
