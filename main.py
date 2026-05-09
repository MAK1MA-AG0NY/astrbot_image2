from __future__ import annotations

import asyncio
import base64
import mimetypes
import uuid
from pathlib import Path
from typing import Any

import httpx

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Reply
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
from astrbot.core.utils.quoted_message.extractor import extract_quoted_message_images


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.storage_dir = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_gpt_image2"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10**9)
    async def on_message(self, event: AstrMessageEvent):
        message = event.message_str.strip()
        if not message:
            return

        normalized = message.lstrip("/").strip()
        if normalized.startswith("draw "):
            prompt = normalized[5:].strip()
        elif normalized.startswith("画图 "):
            prompt = normalized[3:].strip()
        else:
            return

        if not prompt:
            yield event.plain_result("用法：/draw 描述词")
            event.stop_event()
            return

        yield event.plain_result("正在生成图片，请稍等…")

        try:
            reply_image = await self._find_reply_image(event)
            if reply_image:
                image_url_or_path = await self._edit_image(prompt, reply_image)
            else:
                image_url_or_path = await self._generate_image(prompt)

            if not image_url_or_path:
                yield event.plain_result("没有拿到图片结果")
                event.stop_event()
                return

            yield event.image_result(image_url_or_path)
            event.stop_event()
        except Exception as exc:
            logger.exception("gpt-image-2 生成失败")
            yield event.plain_result(f"生成失败：{exc}")
            event.stop_event()

    def _get_api_key(self) -> str:
        return str(self.config.get("api_key", "")).strip()

    def _get_auth_header(self) -> str:
        return str(self.config.get("auth_header", "Authorization"))

    def _get_model(self) -> str:
        return str(self.config.get("model", "gpt-image-2")).strip() or "gpt-image-2"

    def _get_timeout(self) -> float:
        return float(self.config.get("timeout_seconds", 180))

    def _get_retry_times(self) -> int:
        try:
            return max(0, int(self.config.get("retry_times", 1)))
        except Exception:
            return 1

    def _get_proxy(self) -> str:
        return str(self.config.get("proxy", "")).strip()

    def _get_generation_url(self) -> str:
        api_url = str(self.config.get("image_api_url", "")).strip()
        if api_url:
            return api_url
        base_url = str(self.config.get("base_url", "")).strip()
        if base_url:
            return base_url.rstrip("/") + "/v1/images/generations"
        return ""

    def _get_edit_url(self) -> str:
        api_url = str(self.config.get("image_edit_api_url", "")).strip()
        if api_url:
            return api_url
        base_url = str(self.config.get("base_url", "")).strip()
        if base_url:
            return base_url.rstrip("/") + "/v1/images/edits"
        return ""

    def _auth_headers(self) -> dict[str, str]:
        api_key = self._get_api_key()
        if not api_key:
            raise ValueError("请先在插件配置中填写 api_key")

        headers = {}
        if self._get_auth_header() == "x-api-key":
            headers["x-api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    async def _generate_image(self, prompt: str) -> str | None:
        api_url = self._get_generation_url()
        if not api_url:
            raise ValueError("请先在插件配置中填写 base_url 或 image_api_url")

        payload: dict[str, Any] = {
            "model": self._get_model(),
            "prompt": prompt,
        }

        async with httpx.AsyncClient(timeout=self._get_timeout(), proxy=self._get_proxy() or None) as client:
            response = await self._post_with_retry(
                client,
                api_url,
                action="生成",
                headers=self._auth_headers(),
                json=payload,
            )
            data = self._parse_response_json(response, "生成")

        return self._extract_image_output(data)

    async def _edit_image(self, prompt: str, image_path: str) -> str | None:
        api_url = self._get_edit_url()
        if not api_url:
            raise ValueError("请先在插件配置中填写 base_url 或 image_edit_api_url")

        image_file = Path(image_path)
        if not image_file.exists():
            raise ValueError(f"找不到引用图片：{image_path}")

        mime_type = mimetypes.guess_type(image_file.name)[0] or "image/png"
        form_data = {"model": self._get_model(), "prompt": prompt}
        files = {
            "image": (
                image_file.name,
                image_file.read_bytes(),
                mime_type,
            )
        }

        async with httpx.AsyncClient(timeout=self._get_timeout(), proxy=self._get_proxy() or None) as client:
            response = await self._post_with_retry(
                client,
                api_url,
                action="编辑",
                headers=self._auth_headers(),
                data=form_data,
                files=files,
            )
            data = self._parse_response_json(response, "编辑")

        return self._extract_image_output(data)

    async def _post_with_retry(
        self,
        client: httpx.AsyncClient,
        api_url: str,
        action: str,
        **kwargs: Any,
    ) -> httpx.Response:
        retry_times = self._get_retry_times()
        last_error: Exception | None = None

        for attempt in range(retry_times + 1):
            try:
                return await client.post(api_url, **kwargs)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_error = exc
                if attempt >= retry_times:
                    break
                logger.warning(
                    "%s请求失败，准备重试 (%s/%s): %s",
                    action,
                    attempt + 1,
                    retry_times + 1,
                    exc,
                )
                await asyncio.sleep(min(2, attempt + 1))

        raise RuntimeError(f"{action}请求失败：{last_error}") from last_error

    def _parse_response_json(self, response: httpx.Response, action: str) -> Any:
        request_url = str(response.request.url)
        status_code = response.status_code
        content_type = response.headers.get("content-type", "")
        body_text = response.text

        if status_code >= 400:
            logger.error(
                "%s接口失败: status=%s url=%s content_type=%s body=%s",
                action,
                status_code,
                request_url,
                content_type,
                body_text[:2000],
            )
            raise RuntimeError(f"{action}接口返回 {status_code}")

        try:
            return response.json()
        except Exception as exc:
            logger.error(
                "%s接口返回非JSON: status=%s url=%s content_type=%s body=%s",
                action,
                status_code,
                request_url,
                content_type,
                body_text[:2000],
            )
            raise RuntimeError(f"{action}接口返回非JSON响应") from exc

    def _extract_image_output(self, data: Any) -> str | None:
        image_b64 = self._find_first_value(data, "b64_json")
        if isinstance(image_b64, str) and image_b64:
            return self._save_b64_image(image_b64)

        image_url = self._find_first_value(data, "url")
        if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
            return image_url

        return None

    def _find_first_value(self, node: Any, key: str) -> Any:
        if isinstance(node, dict):
            if key in node and node[key] is not None:
                return node[key]
            for value in node.values():
                found = self._find_first_value(value, key)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = self._find_first_value(item, key)
                if found is not None:
                    return found
        return None

    def _save_b64_image(self, image_b64: str) -> str:
        if image_b64.startswith("data:"):
            image_b64 = image_b64.split(",", 1)[-1]

        raw = base64.b64decode(image_b64)
        image_path = self.storage_dir / f"{uuid.uuid4().hex}.png"
        image_path.write_bytes(raw)
        return str(image_path)

    async def _find_reply_image(self, event: AstrMessageEvent) -> str | None:
        reply_component = self._find_reply_component(event)
        if not reply_component:
            return None

        quoted_images = await extract_quoted_message_images(event, reply_component)
        if not quoted_images:
            return None

        return await self._normalize_image_ref(quoted_images[0])

    def _find_reply_component(self, event: AstrMessageEvent) -> Reply | None:
        for comp in event.get_messages():
            if isinstance(comp, Reply):
                return comp
        return None

    async def _normalize_image_ref(self, image_ref: str) -> str:
        if image_ref.startswith("file:///"):
            return image_ref[8:]
        if image_ref.startswith(("http://", "https://")):
            image = Image.fromURL(image_ref)
            return await image.convert_to_file_path()
        if image_ref.startswith("base64://"):
            image = Image(file=image_ref)
            return await image.convert_to_file_path()
        if image_ref.startswith("data:image/"):
            bs64 = image_ref.split(",", 1)[-1]
            temp = self.storage_dir / f"{uuid.uuid4().hex}.png"
            temp.write_bytes(base64.b64decode(bs64))
            return str(temp)
        path = Path(image_ref)
        if path.exists():
            return str(path.resolve())
        return image_ref
