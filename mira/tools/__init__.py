"""
Per-agent tool scoping (Ch. 6.2, 11.1): each specialist agent only sees the
3-6 tools relevant to its domain, never a global pool — this is what keeps
tool-selection accuracy high as domain count grows (Ch. 6, Q4).
"""
from mira.tools.domain_tools import (
    book_service_slot,
    calculate_emi,
    check_loan_eligibility,
    create_listing,
    dealer_inventory_query,
    find_nearest_service_center,
    get_claim_status,
    get_insurance_quote,
    get_obv_valuation,
    search_inventory,
    upsert_lead_score,
)

BUYING_TOOLS = [search_inventory, upsert_lead_score]
SELLING_OBV_TOOLS = [get_obv_valuation, create_listing, search_inventory]
INSURANCE_TOOLS = [get_insurance_quote, get_claim_status]
LOANS_TOOLS = [check_loan_eligibility, calculate_emi]
SERVICE_BOOKING_TOOLS = [find_nearest_service_center, book_service_slot]
DEALER_SUPPORT_TOOLS = [dealer_inventory_query]
SUPPORT_TOOLS = [get_claim_status, find_nearest_service_center]

ALL_TOOLS_BY_AGENT = {
    "buying": BUYING_TOOLS,
    "selling_obv": SELLING_OBV_TOOLS,
    "insurance": INSURANCE_TOOLS,
    "loans": LOANS_TOOLS,
    "service_booking": SERVICE_BOOKING_TOOLS,
    "dealer_support": DEALER_SUPPORT_TOOLS,
    "support": SUPPORT_TOOLS,
}
