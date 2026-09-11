from mira.agents.specialist import make_specialist_node
from mira.agents.supervisor import supervisor_node

SPECIALIST_DOMAINS = [
    "buying", "selling_obv", "insurance", "loans",
    "service_booking", "dealer_support", "support", "general",
]

__all__ = ["supervisor_node", "make_specialist_node", "SPECIALIST_DOMAINS"]
