PREFIX ?= $(HOME)/.local
BINDIR := $(PREFIX)/bin
SCRIPT := scripts/read-pdf-as-images
TARGET := $(BINDIR)/read-pdf-as-images

.PHONY: help install uninstall fmt

help:
	@echo "Targets:"
	@echo "  install    Install CLI to $(TARGET)"
	@echo "  uninstall  Remove installed CLI"
	@echo "  help       Show this message"

install: $(SCRIPT)
	@mkdir -p "$(BINDIR)"
	install -m 0755 "$(SCRIPT)" "$(TARGET)"
	@echo "Installed: $(TARGET)"
	@command -v read-pdf-as-images >/dev/null 2>&1 || \
	  echo "Note: Ensure $(BINDIR) is in your PATH"

uninstall:
	@rm -f "$(TARGET)"
	@echo "Removed: $(TARGET)"

