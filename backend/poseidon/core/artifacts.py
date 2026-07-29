"""``ArtifactStore`` — the object-store seam skills use to persist files.

Doc 02 §3 names ``artifacts`` as one of the four things a :class:`~poseidon.
core.skills.context.SkillContext` carries a skill: "the object store". Doc 07
§1 makes the habitat promise explicit — "MinIO stands in for S3 behind the
same ``boto3`` client (endpoint-url config) — artifact code is identical in
every habitat" — so this module never branches on ``deploy_mode``: the same
:class:`ArtifactStore` talks to the local MinIO container, an in-service MinIO
under SPCS, or real S3 on EC2, and the only thing that differs is which
values :class:`~poseidon.core.config.Settings` handed it.

**Path-style addressing.** MinIO (and most self-hosted S3-compatible stores)
has no wildcard DNS for ``<bucket>.<host>`` virtual-hosted-style URLs, so this
client is configured for path-style (``<host>/<bucket>/<key>``) — the one
addressing style that works against both MinIO and real S3.

**Static credentials are a local-only convenience.** ``Settings.s3_access_key``
/ ``s3_secret_key`` exist purely so the local MinIO container (fixed
``MINIO_ROOT_USER``/``MINIO_ROOT_PASSWORD``, docker-compose.yml) has something
to authenticate with; doc 07 §6's environment contract names no such variables
for SPCS (OAuth token) or EC2 (IAM instance profile) because those habitats
never need static keys. When both are unset (``None``) they are passed
through to ``boto3.client(...)`` unchanged, which is precisely how you tell
boto3 "no override — use your normal credential chain" (env vars, instance
profile, etc.), so this module needs no habitat branch for that case either.

**Presigned GETs, never a proxying download endpoint.** :meth:`put_pdf`
returns an :class:`~poseidon.core.skills.context.ArtifactRef` whose ``url`` is
a presigned GET the browser fetches directly — the backend is never in the
path of the file's bytes after upload.

**No run-log recording here.** ``put`` intentionally records nothing beyond
the object itself; the run-log recorder (doc 06) that will note "skill X
produced artifact Y" arrives in Phase 11 and hangs off ``ctx.run``, not off
the store.
"""

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from poseidon.core.config import Settings
from poseidon.core.skills.context import ArtifactRef

# MinIO ignores the region entirely, but botocore's SigV4 signer requires
# *some* value to sign with — hardcoding one keeps signing deterministic
# rather than relying on boto3's own (version-dependent) fallback behavior.
_SIGNING_REGION = "us-east-1"

_PRESIGNED_GET_EXPIRY_SECONDS = 3600


class ArtifactStore:
    """A thin, habitat-agnostic wrapper over one S3-compatible bucket.

    Built from :class:`~poseidon.core.config.Settings` alone — never a raw
    endpoint/bucket/key triple — so every caller composes it the same way the
    dev skill runner (Phase 3 Task 5) will: ``ArtifactStore(settings)``.
    """

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=_SIGNING_REGION,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def ensure_bucket(self) -> None:
        """Create the configured bucket if it does not already exist.

        Idempotent by construction: ``head_bucket`` is the cheap existence
        check (no ``s3:ListAllMyBuckets`` permission required, unlike
        ``list_buckets``), and only a 404 from that call attempts a create.
        A second, overlapping ``create_bucket`` — another process racing this
        one — is tolerated too: MinIO/S3 answer that with
        ``BucketAlreadyOwnedByYou`` (same credentials) or
        ``BucketAlreadyExists`` (a different owner already holds the name),
        and either way the bucket this store cares about now exists, so both
        are swallowed. Any other error is a real failure and propagates.
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status != 404:
                raise

        try:
            self._client.create_bucket(Bucket=self._bucket)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise

    def put_pdf(self, key: str, content: bytes) -> ArtifactRef:
        """Upload ``content`` as ``key`` and return a presigned-GET reference.

        The presigned URL expires in one hour — long enough for the browser
        that just received it in a chat response to fetch the file, short
        enough that a leaked chat transcript does not leak a standing
        download link.
        """
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=content, ContentType="application/pdf"
        )
        url = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=_PRESIGNED_GET_EXPIRY_SECONDS,
        )
        return ArtifactRef(name=key, url=url, mime="application/pdf")
