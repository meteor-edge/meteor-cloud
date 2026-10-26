from sqlalchemy import select

from app.devices.models import Device, DeviceGroup, DeviceType
from app.tenancy.models import Organization
from scripts.seed import ORG_SLUG, seed


def test_seed_creates_a_repeatable_demo_fleet(db_session):
    seed(db_session)
    seed(db_session)

    organization = db_session.scalar(select(Organization).where(Organization.slug == ORG_SLUG))
    assert organization is not None

    device_names = db_session.scalars(
        select(Device.name)
        .where(Device.organization_id == organization.id)
        .order_by(Device.name)
    ).all()
    assert device_names == ["lab-prototype-01", "store-display-02", "store-gateway-01"]

    assert db_session.scalars(
        select(DeviceType).where(DeviceType.organization_id == organization.id)
    ).all()
    assert db_session.scalars(
        select(DeviceGroup).where(DeviceGroup.organization_id == organization.id)
    ).all()
