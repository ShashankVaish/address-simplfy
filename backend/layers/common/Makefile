# Builds the layer with LINUX/ARM64 wheels regardless of the machine running
# `sam build`.
#
# Without this, `sam build` installs whatever wheels match the *host* -- on a
# Mac that is macOS arm64 -- and pydantic-core (a compiled Rust library) then
# fails to import inside Lambda, which runs Linux. Every route returns 500 and
# the only clue is an ImportError in CloudWatch. Pinning the platform here makes
# the build correct from any laptop, with no Docker.
#
# SAM invokes the target named build-<LayerLogicalId>.

PLATFORM  ?= manylinux2014_aarch64
PYVER     ?= 3.12

build-CommonLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python"
	cp -R python/. "$(ARTIFACTS_DIR)/python/"
	python3 -m pip install \
	  --requirement requirements.txt \
	  --target "$(ARTIFACTS_DIR)/python" \
	  --platform "$(PLATFORM)" \
	  --implementation cp \
	  --python-version "$(PYVER)" \
	  --only-binary=:all: \
	  --upgrade \
	  --quiet
	find "$(ARTIFACTS_DIR)/python" -name "__pycache__" -type d -prune -exec rm -rf {} +
	find "$(ARTIFACTS_DIR)/python" -name "tests" -type d -prune -exec rm -rf {} +
