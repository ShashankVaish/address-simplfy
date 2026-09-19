# Builds the clarifier with LINUX/ARM64 wheels regardless of the host, for
# the same reason as layers/common/Makefile: strands-agents pulls in compiled
# dependencies, and a macOS wheel does not import inside Lambda.
#
# SAM invokes the target named build-<FunctionLogicalId>.

PLATFORM  ?= manylinux2014_aarch64
PYVER     ?= 3.12

build-ClarifierFunction:
	mkdir -p "$(ARTIFACTS_DIR)"
	cp app.py agent.py "$(ARTIFACTS_DIR)/"
	python3 -m pip install 	  --requirement requirements.txt 	  --target "$(ARTIFACTS_DIR)" 	  --platform "$(PLATFORM)" 	  --implementation cp 	  --python-version "$(PYVER)" 	  --only-binary=:all: 	  --upgrade 	  --quiet
	find "$(ARTIFACTS_DIR)" -name "__pycache__" -type d -prune -exec rm -rf {} +
