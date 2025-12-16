PREFIX ?= $(HOME)/.local
BINDIR := $(PREFIX)/bin
SCRIPT := scripts/read-pdf
TARGET := $(BINDIR)/read-pdf
LEGACY_SCRIPT := scripts/read-pdf-as-images
LEGACY_TARGET := $(BINDIR)/read-pdf-as-images
HELPER_SCRIPTS := scripts/read_pdf_text.py scripts/read_pdf_structure.py
HELPER_TARGETS := $(addprefix $(BINDIR)/,$(notdir $(HELPER_SCRIPTS)))

.PHONY: help install uninstall lint test

help:
	@echo "Targets:"
	@echo "  install    Install CLI to $(TARGET) (and legacy wrapper to $(LEGACY_TARGET))"
	@echo "  uninstall  Remove installed CLI"
	@echo "  lint       Run shellcheck on scripts"
	@echo "  test       Generate a tiny PDF and run an integration smoke test"
	@echo "  help       Show this message"

install: $(SCRIPT) $(LEGACY_SCRIPT) $(HELPER_SCRIPTS)
	@mkdir -p "$(BINDIR)"
	install -m 0755 "$(SCRIPT)" "$(TARGET)"
	install -m 0755 "$(LEGACY_SCRIPT)" "$(LEGACY_TARGET)"
	install -m 0755 $(HELPER_SCRIPTS) "$(BINDIR)"
	@echo "Installed: $(TARGET)"
	@echo "Installed legacy wrapper: $(LEGACY_TARGET)"
	@echo "Installed helpers: $(HELPER_TARGETS)"
	@command -v read-pdf >/dev/null 2>&1 || \
	  echo "Note: Ensure $(BINDIR) is in your PATH"

uninstall:
	@rm -f "$(TARGET)" "$(LEGACY_TARGET)" $(HELPER_TARGETS)
	@echo "Removed: $(TARGET), $(LEGACY_TARGET), and helper scripts"

lint:
	@shellcheck $(SCRIPT) $(LEGACY_SCRIPT)

test: install
	@echo "%!\n/Times-Roman findfont 24 scalefont setfont\n72 700 moveto\n(Hello PDF) show\nshowpage" > test.ps
	@ps2pdf test.ps test.pdf
	@read-pdf test.pdf --as-images --pages "1"
	@test -f tmp/pdf_renders/test/page-001.png
	@echo "Integration smoke test passed"
