from datetime import datetime

from pydantic import BaseModel, Field


class TimeToSourceOut(BaseModel):
    median_minutes: float | None = Field(
        description="Median minutes from a shortage being reported to its first confirmed "
        "source; null while no shortage has one."
    )
    shortages_confirmed: int = Field(description="Shortages with a confirmed source.")
    shortages_reported: int


class ResolutionMixOut(BaseModel):
    transfers: int = Field(description="Approved TRANSFER and TRANSFER_SPLIT recommendations.")
    purchases: int = Field(description="Approved BUY recommendations.")
    transfer_share: float | None = Field(description="transfers ÷ (transfers + purchases).")
    purchase_share: float | None = Field(description="purchases ÷ (transfers + purchases).")


class CostAvoidedOut(BaseModel):
    paise: int = Field(
        description="Σ units accepted from hospital transfers × the cheapest supplier unit "
        "price recorded by the match run that chose the source."
    )
    units_priced: int
    units_unpriced: int = Field(
        description="Accepted transfer units whose match run recorded no supplier price; "
        "not counted in `paise`."
    )


class ExpirySavedOut(BaseModel):
    units: int = Field(
        description="Units accepted from hospital transfers that came from batches posted as "
        "surplus before the source request."
    )


class ColdChainComplianceOut(BaseModel):
    deliveries: int = Field(description="Cold-chain shipments recorded DELIVERED or RECONCILED.")
    monitored: int = Field(description="Of those, shipments with at least one sensor reading.")
    with_excursion: int = Field(description="Monitored shipments with an EXCURSION event.")
    with_device_silent: int = Field(
        description="Monitored shipments with a DEVICE_SILENT event (not a breach, a gap)."
    )
    compliance_rate: float | None = Field(
        description="(monitored - with_excursion) ÷ monitored; null with none monitored."
    )


class NetworkMetricsOut(BaseModel):
    """Network-wide figures for the platform admin, each computed from recorded rows only
    (api-and-events.md "Network metrics"). No hospital's unit cost appears or is used."""

    computed_at: datetime
    time_to_confirmed_source: TimeToSourceOut
    resolution_mix: ResolutionMixOut
    procurement_cost_avoided: CostAvoidedOut
    units_saved_from_expiry: ExpirySavedOut
    cold_chain: ColdChainComplianceOut
