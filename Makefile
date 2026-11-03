PY ?= .venv/bin/python

.PHONY: setup test eval all
setup:
	python3 -m venv .venv && $(PY) -m pip install -q pytest

test:
	$(PY) -m pytest -q

eval:
	$(PY) -m evals.run_eval

all: test eval
