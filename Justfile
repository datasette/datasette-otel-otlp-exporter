default:
    @just --list --unsorted

# Run the test suite (needs the datasette phase-1 otel branch checked out in ~/projects/datasette)
test *options:
    uv run --with-editable ~/projects/datasette pytest {{ options }}
