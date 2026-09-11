"""
LangChain @tool-decorated functions per domain (Ch. 11.1, 11.3).

Each function is a strict wrapper: validate input -> call MCP client (or
mock) -> return a validated, schema-conformant output. Agents never see raw
dicts — only these typed tools — which is what makes tool-call correctness
mechanically checkable in the LangSmith eval suite (Ch. 16.2, Ch. 11, Q4).
"""
from __future__ import annotations

import os

from langchain_core.tools import tool

from mira.tools.mcp_client import call_mcp_server, new_id, _stable_seed
from mira.tools.schemas import (
    BookServiceSlotInput, BookServiceSlotOutput,
    CalculateEMIInput, CalculateEMIOutput,
    CheckLoanEligibilityInput, LoanEligibilityOutput,
    CreateListingInput, CreateListingOutput,
    DealerInventoryQueryInput, DealerInventoryQueryOutput,
    FindNearestServiceCenterInput, FindNearestServiceCenterOutput, ServiceCenter,
    GetClaimStatusInput, GetClaimStatusOutput,
    GetInsuranceQuoteInput, GetInsuranceQuoteOutput,
    GetOBVValuationInput, GetOBVValuationOutput,
    SearchInventoryInput, SearchInventoryOutput, InventoryListing,
    UpsertLeadScoreInput, UpsertLeadScoreOutput,
)

MCP_OBV_URL = os.getenv("MCP_OBV_URL", "http://mcp-obv")
MCP_LOANS_URL = os.getenv("MCP_LOANS_URL", "http://mcp-loans")
MCP_INSURANCE_URL = os.getenv("MCP_INSURANCE_URL", "http://mcp-insurance")
MCP_INVENTORY_URL = os.getenv("MCP_INVENTORY_URL", "http://mcp-inventory")
MCP_CRM_URL = os.getenv("MCP_CRM_URL", "http://mcp-crm")


# ---------------------------------------------------------------------------
# OBV — Ch. 12.2
# ---------------------------------------------------------------------------
def _mock_obv(inp: GetOBVValuationInput) -> GetOBVValuationOutput:
    rng = _stable_seed(inp.make, inp.model, inp.year, inp.km_driven, inp.city, inp.condition)
    base = 1_200_000 - (2026 - inp.year) * 90_000 - (inp.km_driven // 1000) * 400
    condition_adj = {"excellent": 1.05, "good": 1.0, "fair": 0.9, "poor": 0.75}[inp.condition]
    recommended = max(50_000, int(base * condition_adj) + rng.randint(-15_000, 15_000))
    factors = [
        f"{2026 - inp.year} year(s) of depreciation applied",
        f"{inp.km_driven:,} km mileage adjustment",
        f"Condition rated '{inp.condition}'",
        f"Regional demand signal for {inp.city}",
    ]
    return GetOBVValuationOutput(
        valuation_low=int(recommended * 0.93),
        valuation_high=int(recommended * 1.07),
        valuation_recommended=recommended,
        confidence=round(rng.uniform(0.78, 0.96), 2),
        valuation_id=new_id("obv"),
        factors=factors,
    )


@tool("get_obv_valuation", args_schema=GetOBVValuationInput)
def get_obv_valuation(**kwargs) -> dict:
    """Get Droom's Orange Book Value (OBV) estimate for a vehicle. This is
    the ONLY source of truth for a valuation figure — never state a price
    that didn't come from this tool's result (Ch. 3.1, Ch. 12.2)."""
    inp = GetOBVValuationInput(**kwargs)
    out = call_mcp_server(MCP_OBV_URL, "get_obv_valuation", inp, GetOBVValuationOutput, _mock_obv)
    return out.model_dump()


# ---------------------------------------------------------------------------
# Loans — Ch. 12.4
# ---------------------------------------------------------------------------
def _mock_loan_eligibility(inp: CheckLoanEligibilityInput) -> LoanEligibilityOutput:
    rng = _stable_seed(inp.user_id, inp.vehicle_price, inp.down_payment, inp.tenure_months)
    if not inp.consent_credit_check:
        raise ValueError("consent_credit_check must be true before eligibility can be checked")
    ltv = (inp.vehicle_price - inp.down_payment) / max(inp.vehicle_price, 1)
    income = inp.declared_monthly_income or 45_000
    score = rng.uniform(0.4, 0.95) - max(0, ltv - 0.8) * 0.5
    if score > 0.75:
        band, eligible = "high", True
    elif score > 0.55:
        band, eligible = "medium", True
    elif score > 0.35:
        band, eligible = "low", True
    else:
        band, eligible = "not_eligible", False

    max_loan = int((inp.vehicle_price - inp.down_payment) * (1.0 if eligible else 0))
    rate = round(rng.uniform(9.5, 14.5), 2) if eligible else None
    factors = [
        f"Loan-to-value ratio computed at {ltv:.0%}",
        f"Declared monthly income considered: ₹{income:,}",
        f"Tenure requested: {inp.tenure_months} months",
    ]
    if not eligible:
        factors.append("Income-to-loan ratio below the minimum threshold for this vehicle price band")

    emi = None
    if eligible and rate:
        emi = _emi(max_loan, rate, inp.tenure_months)

    return LoanEligibilityOutput(
        eligible=eligible,
        eligibility_band=band,  # type: ignore[arg-type]
        max_loan_amount=max_loan if eligible else None,
        indicative_interest_rate_pct=rate,
        indicative_emi=emi,
        factors=factors,
        application_id=new_id("loanapp"),
    )


@tool("check_loan_eligibility", args_schema=CheckLoanEligibilityInput)
def check_loan_eligibility(**kwargs) -> dict:
    """Check loan eligibility and an indicative EMI. Requires explicit,
    logged user consent for the credit check (Ch. 12.4) — never call this
    without `consent_credit_check=True` having been genuinely confirmed by
    the user in this conversation."""
    inp = CheckLoanEligibilityInput(**kwargs)
    out = call_mcp_server(MCP_LOANS_URL, "check_loan_eligibility", inp, LoanEligibilityOutput, _mock_loan_eligibility)
    return out.model_dump()


def _emi(principal: int, annual_rate_pct: float, tenure_months: int) -> int:
    r = (annual_rate_pct / 12) / 100
    if r == 0:
        return int(principal / tenure_months)
    emi = principal * r * (1 + r) ** tenure_months / ((1 + r) ** tenure_months - 1)
    return int(round(emi))


def _mock_calculate_emi(inp: CalculateEMIInput) -> CalculateEMIOutput:
    emi = _emi(inp.principal, inp.annual_interest_rate_pct, inp.tenure_months)
    total_payment = emi * inp.tenure_months
    return CalculateEMIOutput(
        emi=emi,
        total_interest=total_payment - inp.principal,
        total_payment=total_payment,
    )


@tool("calculate_emi", args_schema=CalculateEMIInput)
def calculate_emi(**kwargs) -> dict:
    """Deterministically calculate EMI for a given principal/rate/tenure.
    Always use this instead of computing or estimating an EMI in free text."""
    inp = CalculateEMIInput(**kwargs)
    out = call_mcp_server(MCP_LOANS_URL, "calculate_emi", inp, CalculateEMIOutput, _mock_calculate_emi)
    return out.model_dump()


# ---------------------------------------------------------------------------
# Insurance — Ch. 12.3
# ---------------------------------------------------------------------------
def _mock_insurance_quote(inp: GetInsuranceQuoteInput) -> GetInsuranceQuoteOutput:
    rng = _stable_seed(inp.vehicle_registration_or_specs, inp.city, tuple(sorted(inp.add_ons)))
    base = rng.randint(8_000, 18_000)
    add_on_prices = {"zero_dep": 2200, "engine_protect": 1400, "ncb_protect": 900, "roadside_assist": 500}
    selected = {a: add_on_prices.get(a, 800) for a in inp.add_ons}
    total = base + sum(selected.values())
    return GetInsuranceQuoteOutput(
        base_premium=base,
        add_on_premiums=selected,
        total_premium=total,
        quote_id=new_id("quote"),
        insurer_name=rng.choice(["Droom Assure Partner A", "Droom Assure Partner B"]),
        valid_until="2026-09-04",
    )


@tool("get_insurance_quote", args_schema=GetInsuranceQuoteInput)
def get_insurance_quote(**kwargs) -> dict:
    """Get a real insurance premium quote including selected add-ons. Never
    state a premium figure that didn't come from this tool (Ch. 12.3)."""
    inp = GetInsuranceQuoteInput(**kwargs)
    out = call_mcp_server(MCP_INSURANCE_URL, "get_insurance_quote", inp, GetInsuranceQuoteOutput, _mock_insurance_quote)
    return out.model_dump()


def _mock_claim_status(inp: GetClaimStatusInput) -> GetClaimStatusOutput:
    rng = _stable_seed(inp.claim_id)
    status = rng.choice(["submitted", "under_review", "approved", "settled"])
    next_step = {
        "submitted": "Awaiting surveyor assignment",
        "under_review": "Surveyor report under review by claims team",
        "approved": "Settlement being processed",
        "settled": "Claim closed",
    }[status]
    return GetClaimStatusOutput(status=status, last_updated="2026-08-03", next_step=next_step)  # type: ignore[arg-type]


@tool("get_claim_status", args_schema=GetClaimStatusInput)
def get_claim_status(**kwargs) -> dict:
    """Look up the status of an existing insurance claim. For anything
    beyond status/process info (a claim dispute), inform the user of the
    general process and hand off to a human claims specialist (Ch. 12.3)."""
    inp = GetClaimStatusInput(**kwargs)
    out = call_mcp_server(MCP_INSURANCE_URL, "get_claim_status", inp, GetClaimStatusOutput, _mock_claim_status)
    return out.model_dump()


# ---------------------------------------------------------------------------
# Inventory / listings — Ch. 12.5
# ---------------------------------------------------------------------------
def _mock_search_inventory(inp: SearchInventoryInput) -> SearchInventoryOutput:
    rng = _stable_seed(inp.body_type, inp.similar_to_model, inp.transmission, inp.price_max, inp.city)
    makes = [("Hyundai", "Creta"), ("Maruti Suzuki", "Baleno"), ("Tata", "Nexon"), ("Kia", "Seltos")]
    results = []
    for i in range(min(inp.limit, 3)):
        make, model = rng.choice(makes)
        price = rng.randint(500_000, inp.price_max or 1_500_000)
        results.append(InventoryListing(
            listing_id=new_id("listing"),
            make=make, model=model,
            variant=rng.choice(["Base", "Mid", "Top"]),
            year=rng.randint(2019, 2024),
            price=price,
            km_driven=rng.randint(5_000, 60_000),
            city=inp.city or "Bengaluru",
        ))
    return SearchInventoryOutput(results=results, total_matches=rng.randint(len(results), 40))


@tool("search_inventory", args_schema=SearchInventoryInput)
def search_inventory(**kwargs) -> dict:
    """Search Droom's live vehicle inventory. Summarize at most 3 results
    conversationally in voice (Ch. 12.5, 9.2) — never read out the full list."""
    inp = SearchInventoryInput(**kwargs)
    out = call_mcp_server(MCP_INVENTORY_URL, "search_inventory", inp, SearchInventoryOutput, _mock_search_inventory)
    return out.model_dump()


def _mock_create_listing(inp: CreateListingInput) -> CreateListingOutput:
    return CreateListingOutput(listing_id=new_id("listing"), status="draft")


@tool("create_listing", args_schema=CreateListingInput)
def create_listing(**kwargs) -> dict:
    """Create a draft vehicle listing. Should generally be called with a
    `valuation_id` from a prior get_obv_valuation call so the asking price
    is traceable to a real valuation (Ch. 12.2)."""
    inp = CreateListingInput(**kwargs)
    out = call_mcp_server(MCP_INVENTORY_URL, "create_listing", inp, CreateListingOutput, _mock_create_listing)
    return out.model_dump()


# ---------------------------------------------------------------------------
# Service booking — Ch. 12.6
# ---------------------------------------------------------------------------
def _mock_find_service_centers(inp: FindNearestServiceCenterInput) -> FindNearestServiceCenterOutput:
    rng = _stable_seed(inp.city, inp.pincode)
    centers = [
        ServiceCenter(
            center_id=new_id("svc"),
            name=f"Droom Service Hub {n}",
            address=f"{rng.randint(1,99)} MG Road, {inp.city}",
            distance_km=round(rng.uniform(1.2, 12.0), 1),
            next_available_slot_iso="2026-08-07T10:00:00+05:30",
        )
        for n in range(1, 3)
    ]
    return FindNearestServiceCenterOutput(centers=centers)


@tool("find_nearest_service_center", args_schema=FindNearestServiceCenterInput)
def find_nearest_service_center(**kwargs) -> dict:
    """Find nearby Droom-affiliated service centers with next available slot."""
    inp = FindNearestServiceCenterInput(**kwargs)
    out = call_mcp_server(MCP_CRM_URL, "find_nearest_service_center", inp, FindNearestServiceCenterOutput, _mock_find_service_centers)
    return out.model_dump()


def _mock_book_service(inp: BookServiceSlotInput) -> BookServiceSlotOutput:
    return BookServiceSlotOutput(
        booking_id=new_id("booking"), confirmed=True,
        service_center_name="Droom Service Hub", address=f"MG Road, service center {inp.service_center_id}",
    )


@tool("book_service_slot", args_schema=BookServiceSlotInput)
def book_service_slot(**kwargs) -> dict:
    """Confirm a service booking slot for the user."""
    inp = BookServiceSlotInput(**kwargs)
    out = call_mcp_server(MCP_CRM_URL, "book_service_slot", inp, BookServiceSlotOutput, _mock_book_service)
    return out.model_dump()


# ---------------------------------------------------------------------------
# Lead scoring (background, Ch. 12.7) & dealer support (Ch. 12.6)
# ---------------------------------------------------------------------------
def _mock_upsert_lead_score(inp: UpsertLeadScoreInput) -> UpsertLeadScoreOutput:
    return UpsertLeadScoreOutput(ok=True, crm_record_id=new_id("crm"))


@tool("upsert_lead_score", args_schema=UpsertLeadScoreInput)
def upsert_lead_score(**kwargs) -> dict:
    """Write a lead-qualification score derived from signals already present
    in the conversation (Ch. 12.7) — never used to ask extra interrogation
    questions, only to record passively observed signals."""
    inp = UpsertLeadScoreInput(**kwargs)
    out = call_mcp_server(MCP_CRM_URL, "upsert_lead_score", inp, UpsertLeadScoreOutput, _mock_upsert_lead_score)
    return out.model_dump()


def _mock_dealer_query(inp: DealerInventoryQueryInput) -> DealerInventoryQueryOutput:
    rng = _stable_seed(inp.dealer_id, inp.query_type)
    if inp.query_type == "inventory_count":
        result = {"total_listings": rng.randint(10, 300), "published": rng.randint(5, 250)}
    elif inp.query_type == "lead_status":
        result = {"open_leads": rng.randint(0, 40), "hot_leads": rng.randint(0, 10)}
    else:
        result = {"pending_payout": rng.randint(0, 500_000), "last_payout_date": "2026-07-28"}
    return DealerInventoryQueryOutput(result=result)


@tool("dealer_inventory_query", args_schema=DealerInventoryQueryInput)
def dealer_inventory_query(**kwargs) -> dict:
    """B2B query for dealer inventory/lead/payout data. Only callable in a
    'dealer' or 'dsa' persona session — authorization enforced server-side
    at the MCP layer regardless of what the conversation implies (Ch. 11.5)."""
    inp = DealerInventoryQueryInput(**kwargs)
    out = call_mcp_server(MCP_CRM_URL, "dealer_inventory_query", inp, DealerInventoryQueryOutput, _mock_dealer_query)
    return out.model_dump()
