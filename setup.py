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
    package_data={"projectstack.results": ["schema/*.sql", "templates/*.j2"],
                  "projectstack.llm": ["prompts/*.md"]},
    entry_points={
        "console_scripts": [
            "pstack=projectstack.cli.main:cli",
        ],
    },
)
