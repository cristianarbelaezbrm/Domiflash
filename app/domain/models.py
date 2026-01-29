from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

@dataclass
class Driver:
    driver_id: str
    name: str
    chat_id: int
    is_available: bool = True

@dataclass
class Dispatch:
    dispatch_id: str
    driver_chat_id: int
    customer_chat_id: int
    order: Dict[str, Any]
    status: str = "sent"
    ts: int = 0
    accepted_ts: Optional[int] = None
    rejected_ts: Optional[int] = None
    completed_ts: Optional[int] = None
    reassigned_from: Optional[str] = None
