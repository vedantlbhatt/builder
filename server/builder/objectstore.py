"""S3 SigV4 presigned PUTs, with the standard library only.

Photos never pass through the API: the phone uploads straight to the bucket with a URL
this module signs (docs/social.md, "Storage"). boto is a large dependency for one HMAC
chain, and the chain is short enough to read in full below — which matters, because a
signing bug does not crash. It produces a URL that looks right and is rejected by the
bucket with a 403 the phone cannot explain.

The form implemented is the query-string ("presigned URL") variant of Signature Version 4
with an unsigned payload. `test_objectstore.py` reproduces the worked example from the AWS
documentation byte for byte; that is the only evidence the chain is right, so do not
change the canonicalisation without re-checking against it.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

from .settings import settings

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED = "UNSIGNED-PAYLOAD"


@dataclass(frozen=True)
class ObjectStore:
    endpoint: str
    bucket: str
    region: str
    access_key: str
    secret_key: str


def from_settings() -> ObjectStore | None:
    """The configured store, or None when any required piece is missing.

    All-or-nothing on purpose: an endpoint with no secret would sign every URL with an
    empty key, and every upload would fail at the bucket rather than here.
    """
    s = settings()
    if not (
        s.object_store_endpoint
        and s.object_store_bucket
        and s.object_store_key
        and s.object_store_secret
    ):
        return None
    return ObjectStore(
        endpoint=s.object_store_endpoint.rstrip("/"),
        bucket=s.object_store_bucket,
        region=s.object_store_region or "auto",
        access_key=s.object_store_key,
        secret_key=s.object_store_secret,
    )


def configured() -> bool:
    return from_settings() is not None


def public_url(object_key: str) -> str | None:
    """Where a stored object can be read from, when a public base is configured."""
    base = settings().object_store_public_base.rstrip("/")
    if not base:
        return None
    return f"{base}/{_uri_encode(object_key, encode_slash=False)}"


# ------------------------------------------------------------------------- signing


def _uri_encode(value: str, *, encode_slash: bool = True) -> str:
    # SigV4's URI encoding: unreserved characters pass, everything else is %XX, and
    # the path keeps its slashes while query values do not. `quote` gets this right
    # once told that `~` is safe (RFC 3986 unreserved; Python only added it by default
    # in 3.7, so it is spelled out).
    return quote(value, safe="-_.~/" if encode_slash is False else "-_.~")


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def presign(
    method: str,
    endpoint: str,
    path: str,
    headers: dict[str, str],
    *,
    region: str,
    access_key: str,
    secret_key: str,
    expires: int,
    now: datetime,
) -> str:
    """A presigned URL for `method path` against `endpoint`, valid for `expires` seconds.

    `headers` are the request headers the client must send verbatim (host is added here).
    Signing `content-type` on a PUT is what stops a presigned image slot being filled with
    an HTML document that a browser would then render from the public base.
    """
    parts = urlsplit(endpoint)
    host = parts.netloc
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/s3/aws4_request"

    canonical = {"host": host}
    canonical.update({k.lower(): " ".join(v.split()) for k, v in headers.items()})
    signed_headers = ";".join(sorted(canonical))
    canonical_headers = "".join(f"{k}:{canonical[k]}\n" for k in sorted(canonical))

    query = {
        "X-Amz-Algorithm": ALGORITHM,
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": signed_headers,
    }
    canonical_query = "&".join(
        f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(query.items())
    )
    canonical_uri = _uri_encode(path, encode_slash=False)

    canonical_request = "\n".join(
        [method, canonical_uri, canonical_query, canonical_headers, signed_headers, UNSIGNED]
    )
    string_to_sign = "\n".join(
        [ALGORITHM, amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )

    key = _hmac(("AWS4" + secret_key).encode(), datestamp)
    key = _hmac(key, region)
    key = _hmac(key, "s3")
    key = _hmac(key, "aws4_request")
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    return f"{parts.scheme}://{host}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"


def presign_put(
    key: str,
    content_type: str,
    expires: int = 900,
    *,
    now: datetime | None = None,
    store: ObjectStore | None = None,
) -> str:
    """A presigned PUT for `key` in the configured bucket.

    Path-style addressing (`endpoint/bucket/key`): it is what R2 and MinIO accept without
    per-bucket DNS, and AWS accepts it too. Fifteen minutes by default — long enough for a
    photo over a bad connection, short enough that a leaked URL is not a standing grant.
    """
    store = store or from_settings()
    if store is None:
        raise RuntimeError("object store is not configured")
    return presign(
        "PUT",
        store.endpoint,
        f"/{store.bucket}/{key}",
        {"content-type": content_type},
        region=store.region,
        access_key=store.access_key,
        secret_key=store.secret_key,
        expires=expires,
        now=now or datetime.now(UTC),
    )
