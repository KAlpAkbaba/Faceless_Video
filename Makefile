.PHONY: install test doctor plan probe auth dry run clean

install:
	python -m venv .venv
	.venv/bin/pip install -q -r requirements-dev.txt

test:
	.venv/bin/python -m pytest tests/ -q

doctor:
	.venv/bin/python -m pipeline.cli doctor

plan:
	.venv/bin/python -m pipeline.cli plan

probe:
	.venv/bin/python -m pipeline.cli probe

auth:
	.venv/bin/python -m pipeline.cli auth

# Full render, nothing published, nothing spent on video or voice.
dry:
	.venv/bin/python -m pipeline.cli run --no-upload

run:
	.venv/bin/python -m pipeline.cli run

clean:
	rm -rf work out .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
