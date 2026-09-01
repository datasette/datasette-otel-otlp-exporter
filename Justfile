default:
    @just --list --unsorted

# Run the test suite (needs the datasette phase-1 otel branch checked out in ~/projects/datasette)
#
# --no-project matters: the project venv resolves `datasette` from PyPI, which
# shadows the --with-editable checkout on sys.path - and PyPI's alpha does not
# emit the spans this plugin exports.
test *options:
    uv run --no-project --isolated \
      --with-editable . \
      --with-editable ~/projects/datasette \
      --with pytest --with pytest-asyncio --with opentelemetry-proto \
      pytest {{ options }}
