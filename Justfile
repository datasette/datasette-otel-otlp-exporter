# Demo flow - the point: no OTEL_* env vars anywhere in this file.
#
#   just jaeger    +  just dev  +  just request  -> trace UI at http://localhost:16686
#   just receiver  +  just dev  +  just request  -> span summary in the terminal on Ctrl-C

default:
    @just --list --unsorted

# Run the test suite
test *options:
    uv run pytest {{ options }}

# Test suite plus the cross-plugin coexistence test (pulls datasette-otel-parquet
# from GitHub; without it that one test skips)
test-both *options:
    uv run \
      --with "datasette-otel-parquet @ git+https://github.com/datasette/datasette-otel-parquet" \
      --with duckdb \
      pytest {{ options }}

# Generate demo.db (200-row table) if missing
demo-db:
    @[ -e demo.db ] || sqlite3 demo.db "create table plants(id integer primary key, name text, height_cm real); with recursive n(i) as (select 1 union all select i + 1 from n where i < 200) insert into plants select i, 'plant ' || i, abs(random() % 300) from n;"

# Datasette with the plugin exporting to localhost:4318 - one -s flag, no env vars
dev *options: demo-db
    uv run datasette demo.db \
        -s plugins.datasette-otel-otlp-exporter.endpoint http://localhost:4318 \
        -p 8001 {{ options }}

# Jaeger from its own binary - no Docker. UI on http://localhost:16686
jaeger:
    @command -v jaeger >/dev/null || { echo "No jaeger binary on PATH. Grab one from https://www.jaegertracing.io/download/"; exit 1; }
    @echo "UI: http://localhost:16686 - the OTLP ingest port :4318 has no UI"
    jaeger

# UI-less alternative to Jaeger: datasette's demo receiver, Ctrl-C for a summary
receiver:
    curl -fsSL https://raw.githubusercontent.com/simonw/datasette/asg017/otel-phase1-4-otlp-demo/demos/otel/otlp_receiver.py \
      | uv run --no-project --with opentelemetry-proto python -

# Make a traced request against `just dev`
request path="/demo/plants":
    curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001{{ path }}
