PREFIX ?= $(HOME)/.local
BINDIR := $(PREFIX)/bin
SCRIPT := scripts/read-pdf-as-images
TARGET := $(BINDIR)/read-pdf-as-images

.PHONY: help install uninstall lint test

help:
	@echo "Targets:"
	@echo "  install    Install CLI to $(TARGET)"
	@echo "  uninstall  Remove installed CLI"
	@echo "  lint       Run shellcheck on scripts"
	@echo "  test       Generate a tiny PDF and run an integration smoke test"
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

lint:
	@shellcheck $(SCRIPT)

test: install
	@echo "%!\n/Times-Roman findfont 24 scalefont setfont\n72 700 moveto\n(Hello PDF) show\nshowpage" > test.ps
	@ps2pdf test.ps test.pdf
	@read-pdf-as-images test.pdf --pages "1"
	@test -f tmp/pdf_renders/test/page-001.png
	@echo "Integration smoke test passed"
