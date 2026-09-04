.PHONY: install probe build preview clean

# System dep for pygrib. macOS: brew install eccodes
install:
	pip install -r requirements.txt

# ALWAYS run this before trusting settings.yml. NCEP paths move.
probe:
	python -m pipeline.probe

build:
	python -m pipeline.build
	python -m pipeline.build_temp

# Opening docs/index.html straight from disk fails: browsers block fetch()
# on file:// URLs, so the JSON never loads. Serve it instead.
preview:
	@echo "http://localhost:8000"
	@cd docs && python3 -m http.server 8000

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
