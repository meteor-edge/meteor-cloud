"""The image list: a copy of the registry's tags, kept by its notifications and a periodic comparison."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.adapters import container_registry
from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, NotFoundError
from app.devices.schemas import Page
from app.identity.models import User
from app.ports.registry import ContainerRegistry, RegistryUnavailableError
from app.registry.access import sign_access_token
from app.registry.models import ContainerImage, RegistryNamespace, RegistryToken
from app.registry.schemas import ImageRepositoryResponse, ImageTagResponse
from app.releases.models import Release

logger = logging.getLogger(__name__)

# Fixed pg advisory lock key for the reconciliation ("images" in ASCII).
RECONCILE_LOCK_KEY = 0x696D61676573
_TAG_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")

# Tags that keep the images of releases (RegistryImages); not listed.
PIN_PREFIX = "meteorcloud-pin-"


def pin_tag(digest: str) -> str:
    return PIN_PREFIX + digest.removeprefix("sha256:")


def registry_client(session: Session, settings: Settings) -> ContainerRegistry | None:
    return container_registry(
        settings,
        lambda access: sign_access_token(session, settings, subject="meteorcloud", access=access),
    )


class CatalogService:
    def __init__(
        self, session: Session, settings: Settings | None = None, registry: ContainerRegistry | None = None
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._registry = registry or registry_client(session, self.settings)
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    @property
    def registry(self) -> ContainerRegistry:
        if self._registry is None:
            raise NotFoundError("registry_disabled", "The container registry is not enabled on this platform.")
        return self._registry

    # --- keeping the copy ---------------------------------------------------

    def handle_events(self, events: list[dict[str, Any]]) -> None:
        """Events only say where to look: everything stored is read back from the registry."""
        for event in events:
            target = event.get("target")
            if not isinstance(target, dict):
                continue
            repository, tag = str(target.get("repository") or ""), str(target.get("tag") or "")
            owner = self._owner(repository)
            if owner is None:
                continue
            organization_id, path = owner
            if event.get("action") == "push" and not tag.startswith(PIN_PREFIX) and _TAG_RE.fullmatch(tag):
                actor = event.get("actor")
                pushed_by = self._pushed_by(organization_id, actor.get("name") if isinstance(actor, dict) else None)
                digest = target.get("digest")
                self._wait_for(repository, tag, digest)
                self.sync_tag(
                    organization_id,
                    repository,
                    path,
                    tag,
                    pushed_by=pushed_by,
                    pushed_digest=digest if isinstance(digest, str) else None,
                )
            elif event.get("action") == "delete":
                self._sync_repository(organization_id, repository, path)
            self.session.commit()

    def _wait_for(self, repository: str, tag: str, digest: Any) -> None:
        """The registry announces a push before it writes the tag."""
        for _ in range(10):
            if not isinstance(digest, str) or self.registry.digest(repository, tag) == digest:
                return
            time.sleep(0.2)

    def _owner(self, repository: str) -> tuple[uuid.UUID, str] | None:
        namespace, _, path = repository.partition("/")
        row = self.session.get(RegistryNamespace, namespace) if path and len(path) <= 255 else None
        return (row.organization_id, path) if row is not None and row.organization_id is not None else None

    def _is_deleted(self, repository: str) -> bool:
        """In the namespace of an organization that was deleted; unknown namespaces are left alone."""
        row = self.session.get(RegistryNamespace, repository.partition("/")[0])
        return row is not None and row.organization_id is None

    def _pushed_by(self, organization_id: uuid.UUID, subject: Any) -> uuid.UUID | None:
        try:
            token = self.session.get(RegistryToken, uuid.UUID(str(subject)))
        except ValueError:
            return None
        return token.id if token is not None and token.organization_id == organization_id else None

    def sync_tag(
        self,
        organization_id: uuid.UUID,
        repository: str,
        path: str,
        tag: str,
        *,
        pushed_by: uuid.UUID | None = None,
        pushed_digest: str | None = None,
    ) -> None:
        # Until the commit: what was read here cannot overwrite what another worker reads later.
        lock = func.hashtextextended(f"registry-tag:{repository}:{tag}", 0)
        self.session.execute(select(func.pg_advisory_xact_lock(lock)))
        manifest = self.registry.manifest(repository, tag)
        key = {"organization_id": organization_id, "repository": path, "tag": tag}
        if manifest is None:
            self.session.execute(delete(ContainerImage).filter_by(**key))
            return
        if manifest.digest != pushed_digest:
            # A comparison, or a late event: keep what the push's own event stored.
            if self.session.scalar(select(ContainerImage.digest).filter_by(**key)) == manifest.digest:
                return
            if pushed_digest is not None:
                pushed_by = None  # the tag has moved on: not the push that was announced
        values = {
            "digest": manifest.digest,
            "media_type": manifest.media_type,
            "size_bytes": manifest.size_bytes,
            "platforms": [
                {"os": p.os, "architecture": p.architecture, "variant": p.variant, "size_bytes": p.size_bytes}
                for p in manifest.platforms
            ],
            "pushed_at": datetime.now(UTC),
            "pushed_by_token_id": pushed_by,
        }
        statement = insert(ContainerImage).values(id=uuid.uuid4(), **key, **values)
        self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_container_images_repository_tag", set_={**values, "updated_at": func.now()}
            )
        )

    def _sync_repository(self, organization_id: uuid.UUID, repository: str, path: str) -> None:
        # Read before the registry: a tag pushed meanwhile is not in it, so it is not removed.
        known = {
            tag: (digest, pushed_at)
            for tag, digest, pushed_at in self.session.execute(
                select(ContainerImage.tag, ContainerImage.digest, ContainerImage.pushed_at).where(
                    ContainerImage.organization_id == organization_id, ContainerImage.repository == path
                )
            )
        }
        found = [tag for tag in self.registry.list_tags(repository) if _TAG_RE.fullmatch(tag)]
        tags = [tag for tag in found if not tag.startswith(PIN_PREFIX)]
        for tag in tags:
            stored = known.get(tag)
            if stored is None or stored[0] != self.registry.digest(repository, tag):
                self.sync_tag(organization_id, repository, path, tag)
                self.session.commit()  # one tag's lock at a time
        for tag in known.keys() - set(tags):
            # Only the row that was read: the tag may have been pushed again since.
            digest, pushed_at = known[tag]
            self.session.execute(
                delete(ContainerImage).where(
                    ContainerImage.organization_id == organization_id,
                    ContainerImage.repository == path,
                    ContainerImage.tag == tag,
                    ContainerImage.digest == digest,
                    ContainerImage.pushed_at == pushed_at,
                )
            )

    def _sync_pins(self, organization_id: uuid.UUID, repository: str) -> None:
        """Keep exactly the images that releases use, besides the tagged ones."""
        prefix = f"{self.settings.registry_public_host}/{repository}:"
        pins = {tag for tag in self.registry.list_tags(repository) if tag.startswith(PIN_PREFIX)}
        # After listing the tags: a release saved meanwhile has its pin already.
        digests = self.session.scalars(
            select(Release.spec["image_digest"].astext).where(
                Release.organization_id == organization_id,
                Release.spec["registry"].astext == "meteorcloud",
                Release.spec["image"].astext.startswith(prefix, autoescape=True),
            )
        )
        wanted = {pin_tag(digest): digest for digest in digests if digest}
        for tag in pins - wanted.keys():
            self.registry.delete_tag(repository, tag)
        for tag in wanted.keys() - pins:
            if self.registry.digest(repository, wanted[tag]) is not None:
                self.registry.tag(repository, wanted[tag], tag)

    def _erase(self, repository: str) -> None:
        """Untag everything of a deleted organization; garbage collection frees the data."""
        for tag in self.registry.list_tags(repository):
            self.registry.delete_tag(repository, tag)

    def reconcile(self) -> int:
        """Compare every repository with the registry. Returns how many were compared."""
        repositories = set(self.registry.list_repositories())
        for namespace, path in self.session.execute(
            select(RegistryNamespace.name, ContainerImage.repository)
            .join(ContainerImage, ContainerImage.organization_id == RegistryNamespace.organization_id)
            .distinct()
        ):
            repositories.add(f"{namespace}/{path}")
        compared, self.failed = 0, 0
        for repository in sorted(repositories):
            owner = self._owner(repository)
            try:
                if owner is None:
                    if self._is_deleted(repository):
                        self._erase(repository)
                    continue
                self._sync_repository(owner[0], repository, owner[1])
                self._sync_pins(owner[0], repository)
                self.session.commit()
                compared += 1
            except RegistryUnavailableError as exc:
                # One damaged image must not stop the others.
                self.session.rollback()
                self.failed += 1
                logger.warning("Could not compare repository %s: %s", repository, exc)
        return compared

    # --- organization API ---------------------------------------------------

    def _namespace(self, organization_id: uuid.UUID) -> str:
        name = self.session.scalar(
            select(RegistryNamespace.name).where(RegistryNamespace.organization_id == organization_id)
        )
        if name is None:
            raise NotFoundError("image_not_found", "Image was not found.")
        return name

    def list_repositories(
        self, *, actor: User, organization_id: uuid.UUID, search: str | None, page: int, page_size: int
    ) -> Page[ImageRepositoryResponse]:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require(membership, "image.read")
        where = [ContainerImage.organization_id == organization_id]
        if search:
            where.append(ContainerImage.repository.icontains(search, autoescape=True))
        latest = func.max(ContainerImage.pushed_at)
        rows = self.session.execute(
            select(ContainerImage.repository, func.count(), latest)
            .where(*where)
            .group_by(ContainerImage.repository)
            .order_by(latest.desc(), ContainerImage.repository)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        total = self.session.scalar(select(func.count(func.distinct(ContainerImage.repository))).where(*where))
        return Page[ImageRepositoryResponse](
            items=[
                ImageRepositoryResponse(repository=name, tag_count=count, pushed_at=pushed)
                for name, count, pushed in rows
            ],
            total=total or 0,
            page=page,
            page_size=page_size,
        )

    def list_tags(
        self, *, actor: User, organization_id: uuid.UUID, repository: str, page: int, page_size: int
    ) -> Page[ImageTagResponse]:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require(membership, "image.read")
        where = (ContainerImage.organization_id == organization_id, ContainerImage.repository == repository)
        rows = self.session.execute(
            select(ContainerImage, RegistryToken.name)
            .outerjoin(RegistryToken, RegistryToken.id == ContainerImage.pushed_by_token_id)
            .where(*where)
            .order_by(ContainerImage.pushed_at.desc(), ContainerImage.tag)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = []
        for image, token_name in rows:
            item = ImageTagResponse.model_validate(image)
            item.pushed_by = token_name
            items.append(item)
        total = self.session.scalar(select(func.count()).select_from(ContainerImage).where(*where))
        return Page[ImageTagResponse](items=items, total=total or 0, page=page, page_size=page_size)

    def delete(self, *, actor: User, organization_id: uuid.UUID, repository: str, tag: str | None) -> None:
        """Remove one tag, or every tag of the repository."""
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require(membership, "image.delete")
        statement = select(ContainerImage).where(
            ContainerImage.organization_id == organization_id, ContainerImage.repository == repository
        )
        if tag is not None:
            statement = statement.where(ContainerImage.tag == tag)
        images = {image.tag: image for image in self.session.scalars(statement)}
        if not images:
            raise NotFoundError("image_not_found", "Image was not found.")
        full_name = f"{self._namespace(organization_id)}/{repository}"
        tags = list(images)
        if tag is None:
            try:
                # Also the tags whose notification was missed, but not what keeps the images of releases.
                listed = [name for name in self.registry.list_tags(full_name) if not name.startswith(PIN_PREFIX)]
            except RegistryUnavailableError as exc:
                raise AppError("registry_unavailable", str(exc), status_code=503) from exc
            tags += [name for name in listed if name not in images and _TAG_RE.fullmatch(name)]
        for name in tags:
            image = images.get(name)
            try:
                self.registry.delete_tag(full_name, name)
            except RegistryUnavailableError as exc:
                self.session.rollback()
                raise AppError("registry_unavailable", str(exc), status_code=503) from exc
            if image is not None:
                self.session.delete(image)
            self.audit.record(
                actor=actor,
                organization_id=organization_id,
                action="image.delete",
                resource_type="image",
                resource_id=image.id if image is not None else None,
                metadata={
                    "repository": repository,
                    "tag": name,
                    # As last seen: the tag may have moved before it was deleted.
                    "catalog_digest": image.digest if image is not None else None,
                },
            )
            self.session.commit()


def _reconcile_with_new_session() -> None:
    from app.core.database import SessionLocal, engine

    # Held on its own connection: reconcile commits after every repository.
    with engine.connect() as lock_connection:
        acquired = lock_connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": RECONCILE_LOCK_KEY})
        lock_connection.commit()
        if not acquired:
            return
        try:
            with SessionLocal() as session:
                CatalogService(session).reconcile()
        finally:
            # Closing the database session drops the lock too.
            lock_connection.invalidate()


async def run_reconciler(interval_seconds: int) -> None:
    """Reconcile now and every ``interval_seconds`` in a worker thread; errors never stop the loop."""
    while True:
        delay = interval_seconds
        try:
            await asyncio.to_thread(_reconcile_with_new_session)
        except RegistryUnavailableError as exc:
            # Usual at start: the registry starts after the API.
            delay = min(interval_seconds, 60)
            logger.warning("Image reconciliation skipped, trying again in %d s: %s", delay, exc)
        except Exception:
            logger.exception("Image reconciliation failed")
        await asyncio.sleep(delay)
