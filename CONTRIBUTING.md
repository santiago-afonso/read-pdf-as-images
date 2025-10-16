# Contributing

Thanks for your interest in improving `read-pdf-as-images`!

## Development Setup
- Install dependencies: Poppler (pdftoppm, pdfinfo), ShellCheck, Ghostscript (for smoke tests)
  - Ubuntu/Debian: `sudo apt-get install -y poppler-utils shellcheck ghostscript`

## Common Tasks
- Lint: `make lint`
- Install CLI locally: `make install` (installs to `~/.local/bin` by default)
- Smoke test: `make test` (creates a tiny PS -> PDF and renders page 1)

## Code Style & Expectations
- Script is POSIX/Bash; keep it portable to Linux environments.
- Stdout should remain empty during normal operation; JSONL manifest is written to stderr.
- Add `--help` updates whenever introducing flags.

## Release Process
- Bump the `VERSION` variable in `scripts/read-pdf-as-images`.
- Update `README.md` if flags/behavior change.

## License
By contributing, you agree that your contributions are licensed under the MIT License.

