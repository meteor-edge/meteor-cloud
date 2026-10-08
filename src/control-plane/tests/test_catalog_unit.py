"""Unit coverage for the new catalog fields, slug allocation, and patch semantics."""

import uuid
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.artifacts.schemas import ArtifactCreate
from app.core.exceptions import ConflictError
from app.devices.models import DeviceGroup, DeviceType
from app.devices.repository import DeviceGroupRepository, DeviceTypeRepository
from app.devices.schemas import (
    DeviceGroupCreateRequest,
    DeviceGroupUpdateRequest,
    DeviceTypeCreateRequest,
    DeviceTypeUpdateRequest,
)
from app.devices.service import FleetService
from app.identity.models import User
from app.tenancy.models import OrganizationRole
from app.tenancy.repository import OrganizationRepository


@pytest.mark.parametrize("schema", [DeviceTypeCreateRequest, DeviceGroupCreateRequest])
def test_catalog_fields_are_normalized_and_metadata_defaults_are_independent(schema):
    first = schema(name="  Gateway  ", slug="  MY-GATEWAY  ", description="  ")
    second = schema(name="Other")
    assert (first.name, first.slug, first.description) == ("Gateway", "my-gateway", None)
    first.metadata["channel"] = "stable"
    assert second.metadata == {}
    assert second.slug is None


@pytest.mark.parametrize("schema", [DeviceTypeCreateRequest, DeviceGroupCreateRequest])
@pytest.mark.parametrize("slug", ["", " ", "-start", "end-", "two--parts", "with/slash", "with_underscore", "a" * 101])
def test_catalog_rejects_invalid_slugs(schema, slug):
    with pytest.raises(ValidationError):
        schema(name="Gateway", slug=slug)


@pytest.mark.parametrize("field,limit", [("manufacturer", 120), ("model", 120), ("architecture", 64)])
def test_hardware_text_accepts_boundary_and_rejects_overflow(field, limit):
    assert getattr(DeviceTypeCreateRequest(name="Gateway", **{field: "x" * limit}), field) == "x" * limit
    with pytest.raises(ValidationError):
        DeviceTypeCreateRequest(name="Gateway", **{field: "x" * (limit + 1)})
    assert getattr(DeviceTypeCreateRequest(name="Gateway", **{field: "  arm64  "}), field) == "arm64"


@pytest.mark.parametrize("version", ["1", "v1.2.3-rc_1+build.9", "2026.10.07", "a" * 64])
def test_artifact_version_accepts_supported_formats(version):
    assert ArtifactCreate(name="OS", version=version).version == version


@pytest.mark.parametrize("version", [" ", "../1", "-1", "1/2", "1\\2", "1 beta", "é1", "a" * 65])
def test_artifact_version_rejects_unsafe_or_oversized_values(version):
    with pytest.raises(ValidationError):
        ArtifactCreate(name="OS", version=version)


def test_artifact_metadata_normalization_and_independent_defaults():
    first = ArtifactCreate(name=" OS ", version=" 1.0 ", description="  ")
    second = ArtifactCreate(name="Other", version="2")
    assert (first.name, first.version, first.description) == ("OS", "1.0", None)
    first.metadata["channel"] = "stable"
    assert second.metadata == {}
    with pytest.raises(ValidationError):
        ArtifactCreate(name="  ", version="1")
    with pytest.raises(ValidationError):
        ArtifactCreate(name="OS", version="1", metadata=[])


@pytest.fixture(params=["type", "group"])
def catalog(request):
    service = FleetService(Mock(spec=Session))
    service.organizations = Mock(spec=OrganizationRepository)
    service.organizations.get_for_user.return_value = (Mock(), Mock(role=OrganizationRole.OWNER, status="active"))
    service.authz = Mock()
    service.authz.require = Mock()
    service.authz.scope_for.return_value = Mock(mode="organization", device_group_ids=())
    now = datetime.now(UTC)
    common = dict(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name="Original",
        slug="original",
        description="Existing description",
        metadata_={"channel": "stable"},
        created_at=now,
        updated_at=now,
    )
    if request.param == "type":
        item = DeviceType(**common, manufacturer="Maker", model="Model", architecture="arm64", capabilities={})
        repository = service.device_types = Mock(spec=DeviceTypeRepository)
        service.artifacts = Mock()
        service.artifacts.count_by_device_type.return_value = {}
        schema = DeviceTypeUpdateRequest
        update = service.update_device_type
    else:
        item = DeviceGroup(**common, labels={})
        repository = service.device_groups = Mock(spec=DeviceGroupRepository)
        schema = DeviceGroupUpdateRequest
        update = service.update_device_group
    repository.get.return_value = item
    repository.get_by_name.return_value = None
    repository.get_by_slug.return_value = None
    repository.count_devices.return_value = 0
    args = {"actor": User(id=uuid.uuid4()), "organization_id": item.organization_id, f"{request.param}_id": item.id}
    return service, repository, item, schema, update, args


def test_catalog_rename_preserves_slug_and_omitted_fields(catalog):
    service, repository, item, schema, update, args = catalog
    result = update(**args, payload=schema(name="Renamed"))
    assert result.name == "Renamed"
    assert result.slug == "original"
    assert result.description == "Existing description"
    assert result.metadata == {"channel": "stable"}
    repository.get_by_slug.assert_not_called()
    service.session.commit.assert_called_once()


def test_catalog_explicit_null_clears_description_and_empty_metadata_clears_values(catalog):
    _, _, _, schema, update, args = catalog
    result = update(**args, payload=schema(description=None, metadata={}))
    assert result.description is None
    assert result.metadata == {}
    assert result.slug == "original"


def test_catalog_null_metadata_and_slug_leave_existing_values(catalog):
    _, _, _, schema, update, args = catalog
    result = update(**args, payload=schema(metadata=None, slug=None))
    assert result.metadata == {"channel": "stable"}
    assert result.slug == "original"


def test_catalog_explicit_slug_collision_does_not_commit(catalog):
    service, repository, item, schema, update, args = catalog
    repository.get_by_slug.return_value = Mock()
    with pytest.raises(ConflictError) as exc:
        update(**args, payload=schema(slug="taken"))
    assert exc.value.code in {"device_type_slug_exists", "device_group_slug_exists"}
    assert item.slug == "original"
    service.session.commit.assert_not_called()


@pytest.mark.parametrize(
    "name,fallback,expected",
    [
        ("Gateway!", "device-type", "gateway-3"),
        ("!!!", "device-group", "device-group-3"),
        ("a" * 120, "device-type", "a" * 90 + "-3"),
    ],
)
def test_generated_slug_handles_collisions_empty_names_and_truncation(catalog, name, fallback, expected):
    service, repository, item, _, _, _ = catalog
    repository.get_by_slug.side_effect = [Mock(), Mock(), None]
    result = service._resolve_slug(
        requested=None,
        name=name,
        fallback=fallback,
        repository=repository,
        organization_id=item.organization_id,
        conflict_code="slug_exists",
    )
    assert result == expected
    base = expected.removesuffix("-3")
    assert [call.kwargs["slug"] for call in repository.get_by_slug.call_args_list] == [base, f"{base}-2", expected]
    assert all(call.kwargs["organization_id"] == item.organization_id for call in repository.get_by_slug.call_args_list)


@pytest.mark.parametrize("catalog", ["type"], indirect=True)
def test_hardware_fields_can_be_cleared_individually(catalog):
    _, _, _, schema, update, args = catalog
    result = update(**args, payload=schema(manufacturer=None, architecture=" "))
    assert result.manufacturer is None
    assert result.architecture is None
    assert result.model == "Model"


def test_catalog_list_uses_counts_and_defaults_missing_entries_to_zero(catalog):
    service, repository, item, _, _, args = catalog
    empty = type(item)(
        id=uuid.uuid4(),
        organization_id=item.organization_id,
        name="Empty",
        slug="empty",
        description=None,
        metadata_={},
        created_at=item.created_at,
        updated_at=item.updated_at,
        **({"capabilities": {}} if isinstance(item, DeviceType) else {"labels": {}}),
    )
    repository.list.return_value = [item, empty]
    repository.device_counts.return_value = {item.id: 3}
    if isinstance(item, DeviceType):
        service.artifacts.count_by_device_type.return_value = {item.id: 2}
        results = service.list_device_types(actor=args["actor"], organization_id=item.organization_id)
        assert [result.artifact_count for result in results] == [2, 0]
        service.artifacts.count_by_device_type.assert_called_once_with(organization_id=item.organization_id)
    else:
        results = service.list_device_groups(actor=args["actor"], organization_id=item.organization_id)
    assert [result.device_count for result in results] == [3, 0]
    repository.device_counts.assert_called_once_with(organization_id=item.organization_id)
    # A zero from the aggregate must not trigger a count query for every empty row.
    repository.count_devices.assert_not_called()
