from setuptools import setup, find_packages

setup(
    name="homelab",
    version="2.0.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "homelab=homelab.main:main",
        ],
    },
    install_requires=["questionary", "uptime-kuma-api", "cryptography", "speedtest-cli"],
    python_requires=">=3.7",
)
