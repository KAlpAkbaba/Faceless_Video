.PHONY: install test doctor plan probe auth storyboard dry run clean

install:
	python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || \
		{ echo "Python 3.11+ required; found $$(python3 --version)"; exit 1; }
	python3 -m venv .venv
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

storyboard:
	.venv/bin/python -m pipeline.cli storyboard

# Full render, nothing published, nothing spent on video or voice.
dry:
	.venv/bin/python -m pipeline.cli run --no-upload

run:
	.venv/bin/python -m pipeline.cli run

clean:
	rm -rf work out .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
