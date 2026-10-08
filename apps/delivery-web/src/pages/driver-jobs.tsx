// Driver jobs (apps-ai-iot.md, delivery-web `/driver`): the signed-in driver's shipments
// (`assigned_to_me=true`), one big button for the next step each allows, and the "Share
// location" toggle. Built for a phone held in one hand, down to 360 px.
import { type ReactNode, useMemo } from "react";
import { Link } from "react-router";
import {
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Loading,
  PageHeader,
  useCan,
  useProducts,
} from "@care-e/ui";
import { type Shipment, useMyJobs } from "../api";
import { ShareLocation } from "../components/share-location";
import { ShipmentSummary } from "../components/shipment-summary";
import { StepButton } from "../components/step-button";
import { isOnTheWay, SHIPMENT_UPDATE_STATUS } from "../display";

function JobCard({ job, children }: { job: Shipment; children?: ReactNode }) {
  const products = useProducts();
  return (
    <li data-testid={`job-${job.id}`}>
      <Card className="py-4">
        <CardContent className="grid gap-3 px-4">
          <ShipmentSummary shipment={job} product={products.data?.byId.get(job.product_id)} />
          {job.vehicle && (
            <p className="text-sm text-muted-foreground">Vehicle {job.vehicle.reg_no}</p>
          )}
          {children}
          <Link to={`/shipments/${job.id}`} className="text-sm text-primary hover:underline">
            Map and details
          </Link>
        </CardContent>
      </Card>
    </li>
  );
}

export function DriverJobsPage() {
  const isDriver = useCan(SHIPMENT_UPDATE_STATUS);
  const jobs = useMyJobs(isDriver);
  const products = useProducts();
  const active = useMemo(() => jobs.data?.filter((j) => isOnTheWay(j.status)) ?? [], [jobs.data]);
  const activeIds = useMemo(() => active.map((j) => j.id), [active]);
  const done = jobs.data?.filter((j) => !isOnTheWay(j.status)) ?? [];

  const header = <PageHeader title="My jobs" description="Shipments assigned to you." />;
  if (!isDriver)
    return (
      <>
        {header}
        <EmptyState title="For drivers">Only drivers have jobs here.</EmptyState>
      </>
    );
  if (jobs.isPending)
    return (
      <>
        {header}
        <Loading label="Loading your jobs…" />
      </>
    );
  if (jobs.isError)
    return (
      <>
        {header}
        <ErrorState error={jobs.error} />
      </>
    );

  return (
    <>
      {header}
      {active.length > 0 && <ShareLocation shipmentIds={activeIds} />}
      {active.length === 0 ? (
        <EmptyState title="No jobs on the way">
          Your dispatcher assigns shipments to you; they appear here.
        </EmptyState>
      ) : (
        <ul className="grid gap-3" aria-label="Jobs on the way">
          {active.map((job) => (
            <JobCard key={job.id} job={job}>
              <StepButton shipment={job} product={products.data?.byId.get(job.product_id)} />
            </JobCard>
          ))}
        </ul>
      )}
      {done.length > 0 && (
        <section className="mt-6" aria-label="Completed jobs">
          <h2 className="mb-2 text-base font-semibold">Completed</h2>
          <ul className="grid gap-3">
            {done.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
