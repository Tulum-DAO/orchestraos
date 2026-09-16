from datetime import datetime
from .protocol import (
    ProviderAdapter, ProcessReceipt, TranscriptReceipt, DeliveryReceipt, 
    RetirementReceipt, RepinReceipt, ContextObservation, ProviderIdentity, CommandSpec
)

class GeminiAdapter(ProviderAdapter):
    def observe_context(self, seat: str) -> ContextObservation:
        return ContextObservation(token_count=500000, context_pct=0.5, is_near_limit=False)

    def spawn_alias(self, seat: str, successor_alias: str) -> ProcessReceipt:
        return ProcessReceipt(
            provider="gemini", runtime="python", session_id="mock_sid", pid=1002,
            tmux_session="mock_tmux", cwd="/tmp", model="gemini-1.5-pro",
            observed_at=datetime.utcnow(), is_alive=True
        )

    def resolve_declared_identity(self, alias: str) -> ProviderIdentity:
        return ProviderIdentity(session_id="mock_sid", alias=alias)

    def locate_transcript(self, identity: ProviderIdentity) -> TranscriptReceipt:
        return TranscriptReceipt(
            provider="gemini", session_id=identity.session_id,
            transcript_path=f"~/.gemini/antigravity-cli/brain/{identity.session_id}/transcript.jsonl",
            token_count=500000, context_pct=0.5, is_fresh=True
        )

    def resume_command(self, identity: ProviderIdentity) -> CommandSpec:
        return CommandSpec(
            command=f"agy --conversation {identity.session_id}",
            env={}
        )

    def deliver(self, alias_or_sid: str, payload: str, idempotency_key: str) -> DeliveryReceipt:
        return DeliveryReceipt(
            provider="gemini", target_alias_or_sid=alias_or_sid,
            payload_hash="hash", delivered_at=datetime.utcnow(),
            delivery_method="stdio", verified_ack=True
        )

    def observe_process(self, alias_or_sid: str) -> ProcessReceipt:
        return ProcessReceipt(
            provider="gemini", runtime="python", session_id=alias_or_sid, pid=1002,
            tmux_session="mock_tmux", cwd="/tmp", model="gemini-1.5-pro",
            observed_at=datetime.utcnow(), is_alive=True
        )

    def retire_process(self, archive_identity: str) -> RetirementReceipt:
        return RetirementReceipt(
            requested_target=archive_identity, archive_id="arch_1",
            resolved_sid="mock_sid", resolved_pid=1002, action_result="killed",
            postcondition_verified=True, reason="manual"
        )
