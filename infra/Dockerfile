# Poseidon production image -- Phase 14 Task 5, doc 07 section 2.
#
# ONE image serves both halves of the app: the FastAPI backend and the built
# SPA, from the same origin (the backend mounts STATIC_DIR at "/" -- see
# poseidon/api/app.py's own mount comment). That is what removes the second
# static host, the cross-origin preflight, and any CORS allowlist entry for
# the app's own UI from the EC2 deployment entirely.
#
# BUILD CONTEXT IS THE REPO ROOT, not backend/ -- this image needs three
# separate trees (backend/, ontology/, frontend/) and only the root contains
# all three:
#
#   docker build -f infra/Dockerfile -t poseidon:$(git rev-parse --short HEAD) .
#
# Tag convention is poseidon:<git-sha>; rollback is redeploying the previous
# sha's tag, which is why the tag must never be a moving label like :latest.
#
# infra/Dockerfile.dockerignore holds this build's context excludes.
# BuildKit prefers a <dockerfile-name>.dockerignore next to the Dockerfile
# over a context-root .dockerignore, which is exactly why the excludes live
# there: a root .dockerignore would silently have changed the LEGACY root
# ./Dockerfile's build (the Streamlit app, untouched by this phase) too.
#
# The root ./Dockerfile is that legacy Streamlit image and is unrelated to
# this file. Nothing here replaces it and nothing there is read by this one.

# ---------------------------------------------------------------------------
# Stage 1 -- build the SPA.
# ---------------------------------------------------------------------------
# node:22-alpine, pinned to a major rather than :latest, per the infra-polish
# carryforward: a build that silently jumps Node majors between two deploys
# of the same source tree is not a reproducible build.
FROM node:22-alpine AS frontend

WORKDIR /build

# package.json + lockfile first, so `npm ci` re-runs only when dependencies
# actually change -- editing a component does not re-download the tree.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# DISCLOSED DEVIATION from "one image, configured entirely at runtime":
# these three are BUILD-time inputs, not runtime environment. Vite inlines
# import.meta.env.VITE_* into the emitted bundle at build time -- see
# frontend/src/features/auth/auth0Config.ts, which reads all three through
# import.meta.env -- so an Auth0 tenant cannot be supplied to an already-
# built bundle by any amount of container environment. The consequence is
# real and worth stating plainly: an image is bound to ONE Auth0 tenant, and
# pointing a deploy at a different tenant means a rebuild, not a restart.
# The alternative (a runtime-fetched /api/config the SPA reads on boot)
# would keep the image tenant-agnostic; it is a frontend change this task
# does not own. None of these three is a secret: an Auth0 domain, SPA client
# id and API audience are all public values a browser sees anyway.
ARG VITE_AUTH0_DOMAIN
ARG VITE_AUTH0_CLIENT_ID
ARG VITE_AUTH0_AUDIENCE
ENV VITE_AUTH0_DOMAIN=$VITE_AUTH0_DOMAIN \
    VITE_AUTH0_CLIENT_ID=$VITE_AUTH0_CLIENT_ID \
    VITE_AUTH0_AUDIENCE=$VITE_AUTH0_AUDIENCE

# `tsc -b && vite build` (frontend/package.json) -- the type check is part of
# the build on purpose: a type error should fail the image, not ship.
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 -- the runtime image.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# PYTHONUNBUFFERED: this process logs one JSON line per event through
# core/obs.py and prints a handful of boot lines; block-buffered stdout in a
# container means `docker logs` shows nothing until the buffer fills, which
# is indistinguishable from a hung process during a deploy.
# PYTHONDONTWRITEBYTECODE: /app stays root-owned and read-only to the
# non-root runtime user below, so there is nowhere to write .pyc anyway.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# WeasyPrint (build_brief_pdf, Phase 3 Task 4) loads these through ctypes at
# IMPORT time -- pip cannot install them, and without them `import weasyprint`
# raises OSError, not ImportError (hence the `pdf` marker's skip guard in
# poseidon/tasks/.../test_tools.py). This is the dev image's proven set
# (infra/backend.Dockerfile.dev: libpango-1.0-0 libpangoft2-1.0-0 libcairo2
# libgdk-pixbuf-2.0-0 fonts-dejavu-core) UNIONED with doc 07's list, which
# adds libpangocairo-1.0-0 and shared-mime-info -- the union rather than
# either alone because a production image is the wrong place to discover
# which one of two working sets was the minimal one. fonts-dejavu-core gives
# WeasyPrint a real font, so a rendered brief carries text rather than tofu
# boxes.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 \
      libpangoft2-1.0-0 \
      libpangocairo-1.0-0 \
      libcairo2 \
      libgdk-pixbuf-2.0-0 \
      shared-mime-info \
      fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# THE LAYOUT BELOW IS LOAD-BEARING, not a tidy convention.
#
# poseidon.core.ontology.loader resolves the vendored ontology from its OWN
# file location, at a fixed offset: `Path(__file__).resolve().parents[4]`
# (loader.py line 58, and its module docstring spells the walk out). With
# the backend source tree at /app, that walk is
#
#   /app/poseidon/core/ontology/loader.py
#   parents[0]=/app/poseidon/core/ontology  parents[1]=/app/poseidon/core
#   parents[2]=/app/poseidon                parents[3]=/app
#   parents[4]=/                            <- "repo root"
#
# so the ontology MUST land at /ontology for the default path to resolve.
# This is byte-identical to the reasoning behind the dev stack's read-only
# ontology mount (infra/docker-compose.yml lines 55-63); the same offset also
# governs poseidon.scripts.generate_synthetic (parents[3]). Get this wrong
# and the failure is a missing-file crash in the seeder or the first parse,
# not a subtle degradation.
COPY backend/ /app/
COPY ontology/ /ontology/

# The SPA bundle from stage 1. STATIC_DIR=/app/static (set by
# infra/docker-compose.ec2.yml) is what makes create_app mount it.
COPY --from=frontend /build/dist/ /app/static/

# Installs the DEPENDENCIES declared in pyproject.toml. The package copy this
# also drops into site-packages is deliberately NOT what runs -- see the CMD
# comment below.
#
# KNOWN, PARKED GAP named here so nobody rediscovers it the hard way:
# backend/pyproject.toml declares NO package-data, so poseidon/config/
# models.yml and poseidon/config/prompts/**/*.md are not carried into a
# built wheel/sdist at all. A pure site-packages install would therefore
# import fine and then fail at first use, when RoleClient/PromptRegistry
# went looking for files that were never packaged. The source-tree layout
# above sidesteps it entirely (the real files are right there at
# /app/poseidon/...), which is why this image runs from the source tree.
# Fixing pyproject stays parked until a wheel-install image actually
# matters.
RUN pip install /app

# Non-root: nothing this process does needs root, and /app stays root-owned
# (no chown) so the running user cannot modify its own code or the vendored
# ontology at /ontology. Artifacts go to S3, never to the local filesystem.
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000

# `python -m uvicorn`, NEVER the bare `uvicorn` console script -- this is a
# correctness requirement, not a style choice. `python -m` puts the CURRENT
# DIRECTORY (/app, via WORKDIR) at sys.path[0]; a console script puts its own
# bin directory there instead, which would make the site-packages copy of
# `poseidon` win the import. That copy is missing models.yml/prompts/*.md
# (the parked package-data gap above) AND resolves parents[4] to
# /usr/local/lib/python3.12 rather than /, so the ontology would vanish too.
# Running as a module is what makes /app/poseidon -- the complete source tree
# -- the package that actually imports.
#
# --factory: create_app is a factory, not a module-level app object.
# No --reload and no --workers: one process per container, restarts and
# horizontal scale are the orchestrator's job, and the in-process
# ChatRateLimiter's per-sub buckets are per-process state that multiple
# workers would silently fragment.
CMD ["python", "-m", "uvicorn", "poseidon.api.app:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000"]
