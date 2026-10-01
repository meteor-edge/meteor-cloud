import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { fetchHealth, type HealthResponse } from "@/lib/api";

export function HealthPage() {
  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
  });

  return (
    <section className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">System health</h1>
        <p className="mt-2 text-muted-foreground">
          Live status from the backend <code className="text-sm">/health</code> endpoint.
        </p>
      </div>

      <div className="rounded-lg border border-border bg-white/80 p-6 shadow-sm">
        {healthQuery.isLoading && <p className="text-muted-foreground">Checking backend…</p>}

        {healthQuery.isError && (
          <div className="space-y-3">
            <p className="font-medium text-red-700">Backend unreachable</p>
            <p className="text-sm text-muted-foreground">
              {healthQuery.error instanceof Error
                ? healthQuery.error.message
                : "Unknown error while contacting the API."}
            </p>
            <Button variant="secondary" onClick={() => healthQuery.refetch()}>
              Retry
            </Button>
          </div>
        )}

        {healthQuery.data && <HealthDetails data={healthQuery.data} />}
      </div>
    </section>
  );
}

function HealthDetails({ data }: { data: HealthResponse }) {
  return (
    <dl className="grid gap-4 sm:grid-cols-3">
      <div>
        <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Status
        </dt>
        <dd className="mt-1 text-lg font-semibold capitalize text-primary">{data.status}</dd>
      </div>
      <div>
        <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Service
        </dt>
        <dd className="mt-1 text-lg font-medium">{data.service}</dd>
      </div>
      <div>
        <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Version
        </dt>
        <dd className="mt-1 text-lg font-medium">{data.version}</dd>
      </div>
    </dl>
  );
}
