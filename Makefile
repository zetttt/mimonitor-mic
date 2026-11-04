PYTHON ?= python3

.PHONY: test build check guardian bundle
test:
	$(PYTHON) -m unittest discover -s tests -v

build:
	mkdir -p .build
	xcrun swiftc -swift-version 5 src/audio-output.swift -o .build/audio-output
	xcrun swiftc -swift-version 5 -parse-as-library src/service.swift -o .build/mimonitor-mic

check: test build
	bash -n Install.command install.sh mic.sh uninstall.sh

guardian:
	$(PYTHON) guardian/build.py

bundle: guardian
	$(PYTHON) scripts/bundle.py
