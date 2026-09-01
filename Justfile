# Demo flow - the point: no OTEL_* env vars anywhere in this file.
#
#   just jaeger    +  just dev  +  just request  -> trace UI at http://localhost:16686
#   just receiver  +  just dev  +  just request  -> span summary in the terminal on Ctrl-C
#
# Everything runs against an editable checkout of ~/projects/datasette on the
# phase-1 otel branch (asg017/otel-phase1-4-otlp-demo or later) - no released
# datasette emits these spans yet. See PLAN.md.

default:
    @just --list --unsorted

# Run the test suite
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

# Generate demo.db (200-row table) if missing
demo-db:
    @[ -e demo.db ] || sqlite3 demo.db "create table plants(id integer primary key, name text, height_cm real); with recursive n(i) as (select 1 union all select i + 1 from n where i < 200) insert into plants select i, 'plant ' || i, abs(random() % 300) from n;"

# Datasette with the plugin exporting to localhost:4318 - one -s flag, no env vars
dev *options: demo-db
    uv run --no-project --isolated \
      --with-editable . \
      --with-editable ~/projects/datasette \
      datasette demo.db \
        -s plugins.datasette-otel-otlp.endpoint http://localhost:4318 \
        -p 8001 {{ options }}

# Jaeger from its own binary - no Docker. UI on http://localhost:16686
jaeger:
    @command -v jaeger >/dev/null || { echo "No jaeger binary on PATH. Grab one from https://www.jaegertracing.io/download/"; exit 1; }
    @echo "UI: http://localhost:16686 - the OTLP ingest port :4318 has no UI"
    jaeger

# UI-less alternative to Jaeger: datasette's demo receiver, Ctrl-C for a summary
receiver:
    uv run --no-project --with opentelemetry-proto python ~/projects/datasette/demos/otel/otlp_receiver.py

# Make a traced request against `just dev`
request path="/demo/plants":
    curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001{{ path }}
