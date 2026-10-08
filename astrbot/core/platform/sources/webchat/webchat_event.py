import asyncio
import base64
import json
import os
import shutil
from pathlib import Path, PurePosixPath

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import File, Image, Json, Plain, Record
from astrbot.core import db_helper
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.utils.datetime_utils import generate_timestamp_id
from astrbot.core.utils.media_utils import (
    MEDIA_MIME_EXTENSIONS,
    detect_image_mime_type_async,
)

from .message_parts_helper import message_chain_to_storage_message_parts
from .webchat_queue_mgr import (
    _extract_conversation_id,
    orphan_run_registry,
    webchat_queue_mgr,
)

attachments_dir = os.path.join(get_astrbot_data_path(), "attachments")


async def _persist_bot_reply_if_orphan(
    *,
    session_id: str,
    request_id: str,
    message_chain: MessageChain | None = None,
    plain_text: str = "",
    reasoning_text: str = "",
) -> None:
    """Persist a bot reply whose back queue has no dashboard consumer.

    Dashboard-sent turns register their request id on the conversation's back
    queue and ChatService._consume_chat_run persists the bot record. Turns
    injected through the core input queue (e.g. agent collab) have no such
    consumer — without this fallback the reply would only exist in the live
    system stream and disappear from the session history after a reload.
    """
    cid = _extract_conversation_id(session_id)
    if request_id in webchat_queue_mgr.list_back_request_ids(cid):
        return  # consumed by the dashboard stream path; it persists the record
    if message_chain is not None:
        # Telemetry blobs are not user-visible replies: persisting a record
        # that carries only one of them renders as a JSON bubble after a
        # reload. The compression notice is dropped by the dashboard
        # accumulator on the primary path and shown as a toast; orphan turns
        # reach this fallback instead and must drop it here.
        if getattr(message_chain, "type", "") in (
            "agent_stats",
            "context_compression",
        ):
            return
        parts = await message_chain_to_storage_message_parts(
            message_chain,
            insert_attachment=db_helper.insert_attachment,
            attachments_dir=attachments_dir,
        )
    else:
        parts = []
        if reasoning_text:
            parts.append({"type": "think", "think": reasoning_text})
        if plain_text:
            parts.append({"type": "plain", "text": plain_text})
    if not parts:
        return
    try:
        await db_helper.insert_platform_message_history(
            platform_id="webchat",
            user_id=cid,
            content={"type": "bot", "message": parts},
            sender_id="bot",
            sender_name="bot",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            f"[WebChatMessageEvent] Failed to persist orphan bot reply: {exc}",
            exc_info=True,
        )


class WebChatMessageEvent(AstrMessageEvent):
    def __init__(self, message_str, message_obj, platform_meta, session_id) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        os.makedirs(attachments_dir, exist_ok=True)

    @staticmethod
    async def _mirror_system(system_cid: str | None, payload: dict) -> None:
        """Mirror one payload to the conversation-level system event channel.

        Args:
            system_cid: Raw conversation id, or None to skip mirroring.
            payload: The exact payload dict also written to the back queue.
        """
        if system_cid is not None:
            await webchat_queue_mgr.put_system_event(system_cid, payload)
        # Orphan-run resume streams follow the same mirrored payloads.
        orphan_run_registry.publish(payload.get("message_id"), payload)

    @staticmethod
    async def _send(
        message_id: str,
        message: MessageChain | None,
        session_id: str,
        streaming: bool = False,
        emit_complete: bool = False,
        mirror_system: bool = True,
    ) -> str | None:
        request_id = str(message_id)
        system_cid = _extract_conversation_id(session_id) if mirror_system else None
        if not message:
            payload = {
                "type": "end",
                "data": "",
                "streaming": False,
                "message_id": message_id,
            }  # end means this request is finished
            await webchat_queue_mgr.put_back_queue(request_id, payload)
            await WebChatMessageEvent._mirror_system(system_cid, payload)
            return

        data = ""
        for comp in message.chain:
            if isinstance(comp, Plain):
                data = comp.text
                payload = {
                    "type": "plain",
                    "data": data,
                    "streaming": streaming,
                    "chain_type": message.type,
                    "message_id": message_id,
                }
                accepted = await webchat_queue_mgr.put_back_queue(
                    request_id,
                    payload,
                )
                await WebChatMessageEvent._mirror_system(system_cid, payload)
                if not accepted:
                    return None
            elif isinstance(comp, Json):
                payload = {
                    "type": "plain",
                    "data": json.dumps(comp.data, ensure_ascii=False),
                    "streaming": streaming,
                    "chain_type": message.type,
                    "message_id": message_id,
                }
                accepted = await webchat_queue_mgr.put_back_queue(
                    request_id,
                    payload,
                )
                await WebChatMessageEvent._mirror_system(system_cid, payload)
                if not accepted:
                    return None
            elif isinstance(comp, Image):
                # save image to local
                image_base64 = await comp.convert_to_base64()
                image_bytes = base64.b64decode(image_base64)
                mime_type = await detect_image_mime_type_async(
                    image_bytes,
                    default_mime_type=None,
                )
                suffix = MEDIA_MIME_EXTENSIONS.get(mime_type or "", ".jpg")
                filename = f"{generate_timestamp_id()}{suffix}"
                path = os.path.join(attachments_dir, filename)
                await asyncio.to_thread(Path(path).write_bytes, image_bytes)
                data = f"[IMAGE]{filename}"
                payload = {
                    "type": "image",
                    "data": data,
                    "streaming": streaming,
                    "message_id": message_id,
                }
                accepted = await webchat_queue_mgr.put_back_queue(
                    request_id,
                    payload,
                )
                await WebChatMessageEvent._mirror_system(system_cid, payload)
                if not accepted:
                    return None
            elif isinstance(comp, Record):
                # save record to local
                filename = f"{generate_timestamp_id()}.wav"
                path = os.path.join(attachments_dir, filename)
                record_base64 = await comp.convert_to_base64()
                record_bytes = base64.b64decode(record_base64)
                await asyncio.to_thread(Path(path).write_bytes, record_bytes)
                data = f"[RECORD]{filename}"
                payload = {
                    "type": "record",
                    "data": data,
                    "streaming": streaming,
                    "message_id": message_id,
                }
                accepted = await webchat_queue_mgr.put_back_queue(
                    request_id,
                    payload,
                )
                await WebChatMessageEvent._mirror_system(system_cid, payload)
                if not accepted:
                    return None
            elif isinstance(comp, File):
                # save file to local
                file_path = await comp.get_file()
                raw_original_name = comp.name or os.path.basename(file_path)
                original_name = (
                    PurePosixPath(str(raw_original_name).replace("\\", "/"))
                    .name.replace("\x00", "")
                    .strip()
                )
                if original_name in {"", ".", ".."}:
                    original_name = os.path.basename(file_path) or "file"
                ext = os.path.splitext(original_name)[1] or ""
                filename = f"{generate_timestamp_id()}{ext}"
                dest_path = os.path.join(attachments_dir, filename)
                shutil.copy2(file_path, dest_path)
                data = f"[FILE]{filename}|{original_name}"
                payload = {
                    "type": "file",
                    "data": data,
                    "streaming": streaming,
                    "message_id": message_id,
                }
                accepted = await webchat_queue_mgr.put_back_queue(
                    request_id,
                    payload,
                )
                await WebChatMessageEvent._mirror_system(system_cid, payload)
                if not accepted:
                    return None
            else:
                logger.debug(f"webchat 忽略: {comp.type}")

        if emit_complete:
            payload = {
                "type": "complete",
                "data": data,
                "streaming": streaming,
                "chain_type": message.type,
                "message_id": message_id,
            }
            await webchat_queue_mgr.put_back_queue(request_id, payload)
            await WebChatMessageEvent._mirror_system(system_cid, payload)

        return data

    async def send(self, message: MessageChain | None) -> None:
        message_id = self.message_obj.message_id
        follow_up_capture = self.get_extra("_follow_up_captured")
        if message is None and isinstance(follow_up_capture, dict):
            request_id = str(message_id)
            payload = {
                "type": "follow_up_captured",
                "data": follow_up_capture,
                "streaming": False,
                "message_id": message_id,
            }
            await webchat_queue_mgr.put_back_queue(request_id, payload)
            await WebChatMessageEvent._mirror_system(
                _extract_conversation_id(self.session_id), payload
            )
        await WebChatMessageEvent._send(message_id, message, session_id=self.session_id)
        if message is not None:
            await _persist_bot_reply_if_orphan(
                session_id=self.session_id,
                request_id=str(message_id),
                message_chain=message,
            )
        await super().send(MessageChain([]))

    async def send_typing(self) -> None:
        """Emit a run-start signal before an independent LLM request."""
        message_id = self.message_obj.message_id
        request_id = str(message_id)
        payload = {
            "type": "run_started",
            "data": {"run_id": request_id},
            "streaming": False,
            "message_id": message_id,
        }
        await webchat_queue_mgr.put_back_queue(request_id, payload)
        await WebChatMessageEvent._mirror_system(
            _extract_conversation_id(self.session_id), payload
        )

    async def send_streaming(self, generator, use_fallback: bool = False) -> None:
        final_data = ""
        reasoning_content = ""
        message_id = self.message_obj.message_id
        request_id = str(message_id)
        cid = _extract_conversation_id(self.session_id)
        # Turns without a dashboard consumer (goal-loop / collab synthetic
        # turns) register a lightweight orphan run so the active-run recovery
        # (snapshot + resume stream) can re-attach mid-flight.
        is_orphan = request_id not in webchat_queue_mgr.list_back_request_ids(cid)
        if is_orphan:
            orphan_run_registry.register(request_id, cid)
        async for chain in generator:
            # 处理音频流（Live Mode）
            if chain.type == "audio_chunk":
                # 音频流数据，直接发送
                audio_b64 = ""
                text = None

                if chain.chain and isinstance(chain.chain[0], Plain):
                    audio_b64 = chain.chain[0].text

                if len(chain.chain) > 1 and isinstance(chain.chain[1], Json):
                    text = chain.chain[1].data.get("text")

                payload = {
                    "type": "audio_chunk",
                    "data": audio_b64,
                    "streaming": True,
                    "message_id": message_id,
                }
                if text:
                    payload["text"] = text

                accepted = await webchat_queue_mgr.put_back_queue(request_id, payload)
                await WebChatMessageEvent._mirror_system(
                    _extract_conversation_id(self.session_id), payload
                )
                if not accepted:
                    return
                continue

            # if chain.type == "break" and final_data:
            #     # 分割符
            #     await web_chat_back_queue.put(
            #         {
            #             "type": "break",  # break means a segment end
            #             "data": final_data,
            #             "streaming": True,
            #         },
            #     )
            #     final_data = ""
            #     continue

            r = await WebChatMessageEvent._send(
                message_id=message_id,
                message=chain,
                session_id=self.session_id,
                streaming=True,
            )
            if not r:
                continue
            if chain.type == "reasoning":
                reasoning_content += chain.get_plain_text()
            else:
                final_data += r
            if is_orphan:
                snapshot: list[dict] = []
                if reasoning_content:
                    snapshot.append({"type": "think", "think": reasoning_content})
                if final_data:
                    snapshot.append({"type": "plain", "text": final_data})
                orphan_run_registry.update(request_id, snapshot)

        payload = {
            "type": "complete",  # complete means we return the final result
            "data": final_data,
            "reasoning": reasoning_content,
            "streaming": True,
            "message_id": message_id,
        }
        await webchat_queue_mgr.put_back_queue(request_id, payload)
        await WebChatMessageEvent._mirror_system(
            _extract_conversation_id(self.session_id), payload
        )
        if is_orphan:
            # Finish BEFORE the persisted await: the system-stream flush can
            # be scheduled during the DB write and must already see this run
            # as a finished orphan (it skips persisting those).
            orphan_run_registry.finish(request_id)
        await _persist_bot_reply_if_orphan(
            session_id=self.session_id,
            request_id=request_id,
            plain_text=final_data,
            reasoning_text=reasoning_content,
        )
        await super().send_streaming(generator, use_fallback)
