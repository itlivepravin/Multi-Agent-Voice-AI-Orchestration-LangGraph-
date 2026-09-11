"""
Strict Pydantic I/O schemas for every domain tool (Ch. 11.3 of the design doc).

These are deliberately the *only* way numeric/policy facts enter agent
context — Chapter 3.1's core principle ("the LLM converses, it does not
compute business truth") is enforced here at the type level: an agent can
only obtain a valuation, EMI, or eligibility figure by calling one of these
tools and receiving a validated response, never by generating one itself.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# OBV — Orange Book Value (Ch. 12.2)
# ---------------------------------------------------------------------------
class GetOBVValuationInput(BaseModel):
    make: str
    model: str
    variant: Optional[str] = None
    year: int = Field(ge=1990, le=2100)
    km_driven: int = Field(ge=0)
    city: str
    condition: Literal["excellent", "good", "fair", "poor"]


class GetOBVValuationOutput(BaseModel):
    valuation_low: int
    valuation_high: int
    valuation_recommended: int
    confidence: float = Field(ge=0.0, le=1.0)
    valuation_id: str          # audit reference, FR-8
    factors: list[str]         # explainability — Ch. 11.3, Ch. 12.2


# ---------------------------------------------------------------------------
# Loan eligibility (Ch. 12.4)
# ---------------------------------------------------------------------------
class CheckLoanEligibilityInput(BaseModel):
    user_id: str
    vehicle_price: int = Field(ge=0)
    down_payment: int = Field(ge=0)
    tenure_months: int = Field(ge=6, le=84)
    declared_monthly_income: Optional[int] = None
    consent_credit_check: bool  # Ch. 12.4 — must be an explicit, logged consent event


class LoanEligibilityOutput(BaseModel):
    eligible: bool
    eligibility_band: Literal["high", "medium", "low", "not_eligible"]
    max_loan_amount: Optional[int]
    indicative_interest_rate_pct: Optional[float]
    indicative_emi: Optional[int]
    factors: list[str]         # never inferred by the LLM — Ch. 12.4
    application_id: str


class CalculateEMIInput(BaseModel):
    principal: int = Field(ge=0)
    annual_interest_rate_pct: float = Field(ge=0, le=50)
    tenure_months: int = Field(ge=1, le=84)


class CalculateEMIOutput(BaseModel):
    emi: int
    total_interest: int
    total_payment: int


# ---------------------------------------------------------------------------
# Insurance (Ch. 12.3)
# ---------------------------------------------------------------------------
class GetInsuranceQuoteInput(BaseModel):
    vehicle_registration_or_specs: str
    city: str
    add_ons: list[str] = Field(default_factory=list)   # e.g. ["zero_dep", "engine_protect"]


class GetInsuranceQuoteOutput(BaseModel):
    base_premium: int
    add_on_premiums: dict[str, int]
    total_premium: int
    quote_id: str
    insurer_name: str
    valid_until: str


class GetClaimStatusInput(BaseModel):
    claim_id: str
    user_id: str


class GetClaimStatusOutput(BaseModel):
    status: Literal["submitted", "under_review", "approved", "rejected", "settled"]
    last_updated: str
    next_step: str


# ---------------------------------------------------------------------------
# Inventory / listings (Ch. 12.5)
# ---------------------------------------------------------------------------
class SearchInventoryInput(BaseModel):
    body_type: Optional[str] = None
    similar_to_model: Optional[str] = None
    transmission: Optional[Literal["automatic", "manual", "any"]] = "any"
    price_max: Optional[int] = None
    city: Optional[str] = None
    limit: int = Field(default=3, le=10)   # Ch. 12.5 — never read out more than a few results in voice


class InventoryListing(BaseModel):
    listing_id: str
    make: str
    model: str
    variant: str
    year: int
    price: int
    km_driven: int
    city: str


class SearchInventoryOutput(BaseModel):
    results: list[InventoryListing]
    total_matches: int


class CreateListingInput(BaseModel):
    user_id: str
    make: str
    model: str
    variant: Optional[str]
    year: int
    km_driven: int
    city: str
    condition: Literal["excellent", "good", "fair", "poor"]
    asking_price: int
    valuation_id: Optional[str] = None   # links back to the OBV quote that informed the price


class CreateListingOutput(BaseModel):
    listing_id: str
    status: Literal["draft", "published"]


# ---------------------------------------------------------------------------
# CRM / service booking / dealer (Ch. 12.6, 12.7)
# ---------------------------------------------------------------------------
class BookServiceSlotInput(BaseModel):
    user_id: str
    service_center_id: str
    slot_datetime_iso: str
    service_type: str


class BookServiceSlotOutput(BaseModel):
    booking_id: str
    confirmed: bool
    service_center_name: str
    address: str


class FindNearestServiceCenterInput(BaseModel):
    city: str
    pincode: Optional[str] = None


class ServiceCenter(BaseModel):
    center_id: str
    name: str
    address: str
    distance_km: float
    next_available_slot_iso: Optional[str]


class FindNearestServiceCenterOutput(BaseModel):
    centers: list[ServiceCenter]


class UpsertLeadScoreInput(BaseModel):
    user_id: str
    session_id: str
    domain: str
    lead_score: float = Field(ge=0.0, le=1.0)
    signals: list[str]   # e.g. ["budget_stated", "test_drive_interest", "financing_ready"]


class UpsertLeadScoreOutput(BaseModel):
    ok: bool
    crm_record_id: str


class DealerInventoryQueryInput(BaseModel):
    dealer_id: str
    query_type: Literal["inventory_count", "lead_status", "payout_summary"]
    filters: dict = Field(default_factory=dict)


class DealerInventoryQueryOutput(BaseModel):
    result: dict
