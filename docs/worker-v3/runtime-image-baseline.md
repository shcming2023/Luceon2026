# Worker V3 ordinary runtime image baseline

## Decision

`backend/Dockerfile.worker-v3` is the dedicated Worker V3 execution image. The
production path runs deterministic stages and constrained LLM calls. Codex,
App Server, Broker, cross-UID Runner and their credentials are excluded from
the build context and are not a production fallback.

The Compose services `workflow-v3-executor`, `workflow-v3-evaluator`,
`workflow-v3-promoter`, and `workflow-v3-projector` must all use this exact
qualified image digest. None of these roles may silently fall back to the
backend image.

The image is bound by:

- a digest-pinned Python 3.12.13 / Debian 13 base image;
- a fully transitive Python lock with wheel/sdist hashes;
- an exact Debian package-version lock;
- a non-root runtime identity (`uid=gid=10003`);
- an in-image health check that fails closed when a required tool, package
  version, or lock digest drifts.

The package lock pins every explicitly selected runtime capability. The emitted
SBOM records all resolved Debian and Python transitive packages. Because the
configured Debian mirror does not itself guarantee historical retention, the
promoted image digest is the executable baseline: a later source rebuild that
resolves a different transitive Debian package must receive a new identity and
must not be treated as byte-identical.

## Included execution capabilities

The runtime includes the backend application dependencies plus the tools
required by Worker V3 delivery and evaluation:

- XeLaTeX, latexmk, Chinese TeX language support, common LaTeX extras,
  scientific/picture packages and biber;
- Poppler (`pdftoppm`, `pdfinfo`) and PyMuPDF for source/render inspection;
- qpdf and Ghostscript for PDF integrity, page and preview handling;
- Noto CJK/core and TeX Gyre fonts with fontconfig.

This supports Stage 8–11 candidate compilation, independent XeLaTeX
recompilation, PDF raster/inspection, ZIP/PDF delivery checks and visual review
evidence generation. It does not include Codex credentials or the Expert
runtime.

## Build and evidence

Build from the backend context:

```bash
SYSTEM_LOCK_SHA256="$(shasum -a 256 backend/worker-v3-system-packages.lock | awk '{print $1}')"
docker build \
  -f backend/Dockerfile.worker-v3 \
  --build-arg WORKER_V3_SYSTEM_LOCK_SHA256="${SYSTEM_LOCK_SHA256}" \
  --build-arg WORKER_V3_RUNTIME_ID="worker-v3-runtime-<release-id>" \
  --build-arg VCS_REF="<git-sha>" \
  -t luceonweb2026-worker-v3-runtime:<release-id> \
  backend
```

Run the identity gate and write its evidence:

```bash
docker run --rm \
  luceonweb2026-worker-v3-runtime:<release-id> \
  python3 /app/scripts/workflow_v3_runtime_identity.py --check

docker run --rm \
  -v "$PWD/release/worker-v3/runtime:/evidence" \
  luceonweb2026-worker-v3-runtime:<release-id> \
  python3 /app/scripts/workflow_v3_runtime_identity.py \
  --check \
  --output /evidence/ordinary-runtime-identity.json \
  --sbom /evidence/ordinary-runtime-sbom.cdx.json

docker image inspect \
  luceonweb2026-worker-v3-runtime:<release-id> \
  --format '{{json .RepoDigests}} {{.Id}} {{.Size}}'
```

The local BuildKit manifest digest and image ID may be recorded as build
evidence, but the registry digest must be recorded again after pushing because
it is the deployable distribution identity. Deployment must use the resulting
immutable `repository@sha256:...` reference, not a mutable tag.

## Rebuild rule

Changes to the base digest, either lock file, Dockerfile, Worker V3 source,
template release or skill release require a new runtime/release identity. Never
overwrite an existing release tag or reuse an old attestation for a rebuilt
image.
