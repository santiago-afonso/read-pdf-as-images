PREFIX ?= $(HOME)/.local
BINDIR := $(PREFIX)/bin
SCRIPT := scripts/read-pdf
TARGET := $(BINDIR)/read-pdf
LEGACY_SCRIPT := scripts/read-pdf-as-images
LEGACY_TARGET := $(BINDIR)/read-pdf-as-images
HELPER_SCRIPTS := scripts/read_pdf_text.py scripts/read_pdf_structure.py scripts/read_pdf_search.py scripts/read_pdf_page_candidates.py
HELPER_TARGETS := $(addprefix $(BINDIR)/,$(notdir $(HELPER_SCRIPTS)))
PRIME_CACHE ?= 1

.PHONY: help install uninstall lint test prime-cache
.PHONY: install-dev

help:
	@echo "Targets:"
	@echo "  install    Install CLI to $(TARGET) (and legacy wrapper to $(LEGACY_TARGET))"
	@echo "            (default: primes uv cache; set PRIME_CACHE=0 to skip)"
	@echo "  install-dev Symlink CLI + helpers into $(BINDIR) (dev mode: no stale installs)"
	@echo "  uninstall  Remove installed CLI"
	@echo "  prime-cache Prefetch Python deps into uv cache"
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
	@if [ "$(PRIME_CACHE)" != "0" ]; then \
	  echo "Priming uv cache (set PRIME_CACHE=0 to skip)..." ; \
	  READ_PDF_UV_OFFLINE=0 "$(TARGET)" --prime-cache || \
	    echo "Note: cache priming failed (likely no network). Re-run later with: make prime-cache" ; \
	fi

install-dev: $(SCRIPT) $(LEGACY_SCRIPT) $(HELPER_SCRIPTS)
	@mkdir -p "$(BINDIR)"
	ln -sf "$(abspath $(SCRIPT))" "$(TARGET)"
	ln -sf "$(abspath $(LEGACY_SCRIPT))" "$(LEGACY_TARGET)"
	@for f in $(HELPER_SCRIPTS); do \
		ln -sf "$$(cd "$$(dirname "$$f")" && pwd)/$$(basename "$$f")" "$(BINDIR)/$$(basename "$$f")"; \
	done
	@echo "Symlinked: $(TARGET)"
	@echo "Symlinked legacy wrapper: $(LEGACY_TARGET)"
	@echo "Symlinked helpers: $(HELPER_TARGETS)"
	@echo "Note: this is for development; move/rename the repo and the links will break."

uninstall:
	@rm -f "$(TARGET)" "$(LEGACY_TARGET)" $(HELPER_TARGETS)
	@echo "Removed: $(TARGET), $(LEGACY_TARGET), and helper scripts"

prime-cache:
	@$(MAKE) install PRIME_CACHE=0
	@READ_PDF_UV_OFFLINE=0 "$(TARGET)" --prime-cache

lint:
	@shellcheck $(SCRIPT) $(LEGACY_SCRIPT)

test: install
	@mkdir -p tmp
	@echo "%!\n/Times-Roman findfont 24 scalefont setfont\n72 700 moveto\n(Hello PDF) show\nshowpage" > tmp/test.ps
	@ps2pdf tmp/test.ps tmp/test.pdf
	@"$(TARGET)" tmp/test.pdf --as-images --pages "1" > tmp/test.stdout 2> tmp/test.stderr
	@test -f tmp/pdf_renders/test/page-001.png
	@grep -q "gpt-5" tmp/test.stdout
	@grep -q "gemini-3" tmp/test.stdout
	@! grep -q "gpt-5-mini" tmp/test.stdout
	@! grep -q "gpt-5-nano" tmp/test.stdout
	@grep -q '"page":1' tmp/test.stderr
	@"$(TARGET)" tmp/test.pdf --as-images --pages "1" --format jpeg > tmp/test-jpeg.stdout 2> tmp/test-jpeg.stderr
	@test -f tmp/pdf_renders/test/page-001.jpg
	@grep -Fq '"page":1' tmp/test-jpeg.stderr
	@grep -Fq '"format":"jpeg"' tmp/test-jpeg.stderr
	@grep -Fq 'page-001.jpg' tmp/test-jpeg.stderr
	@echo "Integration smoke test passed"
