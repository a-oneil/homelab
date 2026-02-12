from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="homelab",
    version="2.0.0",
    author="Austin ONeil",
    description="A terminal UI for managing self-hosted infrastructure",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/a-oneil/homelab",
    project_urls={
        "Bug Tracker": "https://github.com/a-oneil/homelab/issues",
        "Source Code": "https://github.com/a-oneil/homelab",
    },
    license="MIT",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "homelab=homelab.main:main",
        ],
    },
    install_requires=["questionary", "uptime-kuma-api", "cryptography", "speedtest-cli"],
    python_requires=">=3.7",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Operating System :: MacOS",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Systems Administration",
    ],
    keywords=["homelab", "docker", "ssh", "tui", "self-hosted", "infrastructure"],
)
