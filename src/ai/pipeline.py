from uuid import UUID

from src.utils.logger import logger


async def process_inbound_message(message_id: UUID) -> None:
    """AI pipeline entry point.

    Real implementation lands in the next phase. For now this is a stub
    that just logs so the call-site in business_messages can hook into it.
    """
    logger.debug("AI pipeline triggered for message {} (stub)", message_id)
