from dataclasses import dataclass
from typing import Optional, Any
from abc import ABC, abstractmethod
from datetime import datetime

@dataclass
class ProcessReceipt:
    provider: str
    runtime: str
    session_id: str
    pid: int
    tmux_session: str
    cwd: str
    model: str
    observed_at: datetime
    is_alive: bool
    error_class: Optional[str] = None

@dataclass
class TranscriptReceipt:
    provider: str
    session_id: str
    transcript_path: str
    token_count: int
    context_pct: float
    is_fresh: bool

@dataclass
class DeliveryReceipt:
    provider: str
    target_alias_or_sid: str
    payload_hash: str
    delivered_at: datetime
    delivery_method: str
    verified_ack: bool

@dataclass
class RetirementReceipt:
    requested_target: str
    archive_id: str
    resolved_sid: str
    resolved_pid: int
    action_result: str
    postcondition_verified: bool
    reason: str

@dataclass
class RepinReceipt:
    previous_alias: str
    canonical_name: str
    successor_sid: str
    pane_id: str
    verified_after_rename: bool
    error: Optional[str] = None

@dataclass
class ContextObservation:
    token_count: int
    context_pct: float
    is_near_limit: bool

@dataclass
class ProviderIdentity:
    session_id: str
    alias: str

@dataclass
class CommandSpec:
    command: str
    env: dict[str, str]

class ProviderAdapter(ABC):
    @abstractmethod
    def observe_context(self, seat: str) -> ContextObservation:
        pass

    @abstractmethod
    def spawn_alias(self, seat: str, successor_alias: str) -> ProcessReceipt:
        pass

    @abstractmethod
    def resolve_declared_identity(self, alias: str) -> ProviderIdentity:
        pass

    @abstractmethod
    def locate_transcript(self, identity: ProviderIdentity) -> TranscriptReceipt:
        pass

    @abstractmethod
    def resume_command(self, identity: ProviderIdentity) -> CommandSpec:
        pass

    @abstractmethod
    def deliver(self, alias_or_sid: str, payload: str, idempotency_key: str) -> DeliveryReceipt:
        pass

    @abstractmethod
    def observe_process(self, alias_or_sid: str) -> ProcessReceipt:
        pass

    @abstractmethod
    def retire_process(self, archive_identity: str) -> RetirementReceipt:
        pass
