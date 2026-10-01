import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { FleetNav } from "@/components/fleet/FleetNav";
import { MqttConsole } from "@/components/fleet/MqttConsole";
import { canManageFleet } from "@/lib/permissions";

export function MqttTestPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });
  const canManage = canManageFleet(orgQuery.data?.current_user_role);

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">MQTT test</h1>
        <p className="mt-2 text-muted-foreground">{orgQuery.data?.name}</p>
      </div>
      <FleetNav organizationId={organizationId} />

      <div className="space-y-4 rounded-lg border border-border bg-white/80 p-6 shadow-sm">
        <p className="text-sm text-muted-foreground">
          Send and receive plain text on any MQTT topic. This uses the platform broker
          client (not a device credential). Device-specific examples live on the device page.
        </p>
        {token ? (
          <MqttConsole token={token} organizationId={organizationId} canPublish={canManage} />
        ) : null}
      </div>
    </section>
  );
}
