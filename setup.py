from setuptools import setup, find_packages

setup(
    name="projectstack",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "click>=8.1",
        "requests>=2.28",
        "clickhouse-connect>=0.8",   # results analysis store
        "jinja2>=3.1",               # report templates
    ],
    extras_require={
        # Server metrics from the cloud for results analysis (optional)
        "aws": ["boto3>=1.34"],
        "azure": ["azure-identity>=1.15", "azure-mgmt-monitor>=6.0", "azure-mgmt-resource>=23.0"],
        "cloud": ["boto3>=1.34", "azure-identity>=1.15", "azure-mgmt-monitor>=6.0", "azure-mgmt-resource>=23.0"],
    },
    package_data={"projectstack.results": ["schema/*.sql", "templates/*.j2"],
                  "projectstack.llm": ["prompts/*.md"]},
    entry_points={
        "console_scripts": [
            "pstack=projectstack.cli.main:cli",
        ],
    },
)
