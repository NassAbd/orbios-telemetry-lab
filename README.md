# OrbiOS Telemetry Lab

A local demonstration of natural-language satellite telemetry queries. Ask a question in English, inspect the matched capabilities and validated `tq` query, and see how a deterministic answer is derived from synthetic history. The interface includes a catalogue, a pipeline walkthrough and an explanation for each answer.

**SIMULATION — NO LIVE ORBIOS DATA.** This demo has no connection to operational OrbiOS infrastructure.

## Start the demo

Use Docker Engine 28 or newer with Docker Compose v2 and Linux containers. The first build needs access to Docker Hub, GitHub Container Registry for the public uv base image, PyPI and Hugging Face. Docker downloads the pinned runtime and verifies the MiniLM assets during the build. The application requires only Docker and Compose.

```sh
git clone https://github.com/NassAbd/orbios-telemetry-lab.git
cd orbios-telemetry-lab
docker compose up -d --wait
```

Open [localhost:8765](http://localhost:8765/). Try **Temperature now** or ask “What’s the maximum CPU temperature today?” Choose **Explain this answer** to inspect the operation, time window and synthetic source. **Telemetry Catalogue** shows which measurements are queryable. **How It Works** walks through the pipeline and the three modes.

```sh
docker compose logs -f
docker compose down
```

Use `docker compose restart worker` to reset fictional app edits. Use `docker compose down -v` to stop the demo and remove its Docker-managed socket volume. All tabs in one local instance share the in-memory catalogue. A worker restart restores the original fictional manifests.

For a subsequent start using local images, run `docker compose up -d --wait --no-build --pull never`. To force a rebuild, run `docker compose up -d --wait --build`.

## Platforms and isolation

- **Linux ARM64** was validated on Apple Silicon with OrbStack.
- **Linux AMD64** was validated under emulation. The AMD64 MiniLM export targets AVX2. Native Intel/AMD hosts and Windows Docker Desktop were not tested in this validation.
- The HTTP relay publishes only `127.0.0.1:8765`. It talks to the worker through a Unix socket in a Docker-managed volume.
- The worker has `network_mode: none`, four CPUs, 2 GiB RAM and zero extra swap. Both containers run as an unprivileged user with read-only root filesystems and no host bind mounts. Model weights and dependencies are embedded at build time.

## Tests

Run the API and Docker isolation smoke test on a running local Compose instance.

```sh
python3 tools/check_telemetry_lab_compose.py
```

Run the unit tests inside an isolated container.

```sh
docker build -f Dockerfile.telemetry-lab --target verify -t orbios-lab-verify .
docker run --rm --network none --cpus 4 --memory 2g --memory-swap 2g \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m --cap-drop ALL \
  --security-opt no-new-privileges orbios-lab-verify \
  -m unittest discover -s /bench/tests -p test_interactive_tq.py
```

In the same command, replace the unittest arguments with `-m orbios_qa_bench.interactive_tq.evaluate hybrid` to replay the frozen questions. Use `symbolic` or `minilm` for the other modes. The browser smoke test, `node tools/check_interactive_tq_ui.cjs`, uses a local Playwright installation and Chrome. Set `PLAYWRIGHT_MODULE` or `CHROME_BINARY` when needed.

The simulator models selected read-only `tq` semantics and uses a fixed clock. Validation checks syntax and query constraints. Semantic matching can still select the wrong metric or operation. The AMD64 and ARM64 quantized exports can produce different cosine scores.
