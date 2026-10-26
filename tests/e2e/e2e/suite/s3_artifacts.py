"""Stage 3: control-plane API, MinIO, and artifact storage integrity."""

from __future__ import annotations

import hashlib
import os
import uuid

from e2e.http import multipart, request
from e2e.suite.framework import check, expect

STAGE = "3 api and artifacts"


def upload_artifact(ctx, *, name: str, version: str, kind: str, filename: str, data: bytes,
                    content_type: str = "application/octet-stream") -> dict:
    body, content = multipart({"name": name, "version": version, "type": kind},
                              {"file": (filename, data, content_type)})
    api = ctx.admin()
    api.timeout = 300
    return api.call("POST", ctx.org_path("/artifacts"), form=body, content_type=content, expect=(201,))


@check("api.health", STAGE, "API health endpoint answers")
def api_health(ctx):
    response = request("GET", f"{ctx.api_url}/api/v1/health", ca_file=ctx.cfg.ca_file, timeout=15)
    expect(response.status == 200, f"/api/v1/health -> {response.status}")
    return {"url": ctx.api_url, "body": response.json()}


@check("api.admin_org", STAGE, "Administrator logs in and creates the run's organization", needs=("api.health",))
def admin_org(ctx):
    api = ctx.admin()
    org = api.call("POST", "/api/v1/organizations", json_body={"name": f"E2E {ctx.run_id} A"}, expect=(201,))
    ctx.data["org_id"] = org["id"]
    return {"organization_id": org["id"], "slug": org.get("slug")}


@check("minio.healthy", STAGE, "Bundled MinIO is healthy and the artifact bucket exists", needs=("infra.ssh",))
def minio_healthy(ctx):
    ctx.sh(ctx.cp, f"docker exec {ctx.container('minio')} curl -fsS http://127.0.0.1:9000/minio/health/live")
    result = ctx.backend_python(
        "import json, os, boto3\n"
        "c = boto3.client('s3', endpoint_url=os.environ['OBJECT_STORAGE_ENDPOINT_URL'],"
        " region_name=os.environ.get('OBJECT_STORAGE_REGION') or 'us-east-1',"
        " aws_access_key_id=os.environ['OBJECT_STORAGE_ACCESS_KEY_ID'],"
        " aws_secret_access_key=os.environ['OBJECT_STORAGE_SECRET_ACCESS_KEY'])\n"
        "b = os.environ['OBJECT_STORAGE_BUCKET']\n"
        "c.head_bucket(Bucket=b)\n"
        "print(json.dumps({'bucket': b, 'endpoint': os.environ['OBJECT_STORAGE_ENDPOINT_URL']}))\n"
    )
    return result


@check("minio.not_published", STAGE, "MinIO has no host port mapping", needs=("infra.ssh",))
def minio_not_published(ctx):
    ports = ctx.sh(ctx.cp, f"docker port {ctx.container('minio')}").stdout.strip()
    expect(ports == "", f"minio publishes ports: {ports}")
    listeners = ctx.sh(ctx.cp, "ss -Hltn '( sport = :9000 or sport = :9001 )'").stdout.strip()
    expect(listeners == "", f"host listens on MinIO ports: {listeners}")
    return {"docker_port": ports, "host_listeners": listeners}


@check("artifact.upload", STAGE, "Artifact upload through the API records size and SHA-256",
       needs=("api.admin_org",))
def artifact_upload(ctx):
    data = os.urandom(2 * 1024 * 1024 + 17)
    digest = hashlib.sha256(data).hexdigest()
    artifact = upload_artifact(ctx, name=f"e2e-blob-{ctx.run_id}", version="1", kind="other",
                               filename="blob.bin", data=data)
    expect(artifact["checksum_sha256"] == digest, f"API checksum {artifact['checksum_sha256']} != {digest}")
    expect(artifact["size_bytes"] == len(data), f"API size {artifact['size_bytes']} != {len(data)}")
    ctx.data["blob"] = {"id": artifact["id"], "sha256": digest, "size": len(data), "file_name": artifact["file_name"]}
    return {"artifact_id": artifact["id"], "sha256": digest, "size": len(data)}


@check("artifact.stored_in_minio", STAGE, "The object in MinIO has the same size and SHA-256",
       needs=("artifact.upload", "minio.healthy"))
def stored_in_minio(ctx):
    blob = ctx.data["blob"]
    key = f"organizations/{ctx.data['org_id']}/artifacts/{blob['id']}/{blob['file_name']}"
    result = ctx.backend_python(
        "import hashlib, json, os, boto3\n"
        "c = boto3.client('s3', endpoint_url=os.environ['OBJECT_STORAGE_ENDPOINT_URL'],"
        " region_name=os.environ.get('OBJECT_STORAGE_REGION') or 'us-east-1',"
        " aws_access_key_id=os.environ['OBJECT_STORAGE_ACCESS_KEY_ID'],"
        " aws_secret_access_key=os.environ['OBJECT_STORAGE_SECRET_ACCESS_KEY'])\n"
        f"o = c.get_object(Bucket=os.environ['OBJECT_STORAGE_BUCKET'], Key={key!r})\n"
        "h = hashlib.sha256()\n"
        "for chunk in iter(lambda: o['Body'].read(1 << 20), b''): h.update(chunk)\n"
        "print(json.dumps({'size': o['ContentLength'], 'sha256': h.hexdigest()}))\n"
    )
    expect(result["sha256"] == blob["sha256"], f"MinIO sha256 {result['sha256']} != {blob['sha256']}")
    expect(result["size"] == blob["size"], f"MinIO size {result['size']} != {blob['size']}")
    return {"key": key, **result}


@check("artifact.download", STAGE, "Download through a short-lived link returns identical bytes",
       needs=("artifact.upload",))
def artifact_download(ctx):
    blob = ctx.data["blob"]
    api = ctx.admin()
    link = api.call("POST", ctx.org_path(f"/artifacts/{blob['id']}/download-link"), expect=(200, 201))
    url = link["url"] if link["url"].startswith("http") else ctx.api_url + link["url"]
    body, content = multipart({"ticket": link["ticket"]}, {})
    response = request("POST", url, body=body, headers={"Content-Type": content}, ca_file=ctx.cfg.ca_file,
                       timeout=120)
    expect(response.status == 200, f"download -> {response.status}")
    digest = hashlib.sha256(response.body).hexdigest()
    expect(digest == blob["sha256"], f"downloaded sha256 {digest} != {blob['sha256']}")
    reuse = request("POST", url, body=body, headers={"Content-Type": content}, ca_file=ctx.cfg.ca_file, timeout=60)
    return {"sha256": digest, "bytes": len(response.body), "ticket_reuse_status": reuse.status}


@check("artifact.missing_404", STAGE, "A missing artifact returns 404", needs=("api.admin_org",))
def missing_artifact(ctx):
    status = ctx.admin().status("GET", ctx.org_path(f"/artifacts/{uuid.uuid4()}"))
    expect(status == 404, f"missing artifact -> {status}")
    return {"status": status}


@check("minio.rejects_bad_credentials", STAGE, "MinIO rejects wrong credentials", needs=("minio.healthy",))
def bad_credentials(ctx):
    result = ctx.backend_python(
        "import json, os, boto3, botocore.exceptions as e\n"
        "c = boto3.client('s3', endpoint_url=os.environ['OBJECT_STORAGE_ENDPOINT_URL'], region_name='us-east-1',"
        " aws_access_key_id=os.environ['OBJECT_STORAGE_ACCESS_KEY_ID'], aws_secret_access_key='wrong-secret-e2e')\n"
        "try:\n"
        "    c.list_objects_v2(Bucket=os.environ['OBJECT_STORAGE_BUCKET'], MaxKeys=1)\n"
        "    print(json.dumps({'code': 'allowed'}))\n"
        "except e.ClientError as exc:\n"
        "    print(json.dumps({'code': exc.response['Error']['Code']}))\n"
    )
    expect(result["code"] in ("SignatureDoesNotMatch", "InvalidAccessKeyId", "AccessDenied"),
           f"wrong secret -> {result['code']}")
    return result
