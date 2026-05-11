from __future__ import annotations

import asyncio
import base64
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


class RightCodeDrawClient:
    def __init__(self, config: AstrBotConfig, storage_dir: Path) -> None:
        self._config = config
        self._storage_dir = storage_dir

    def _get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def _base_url(self) -> str:
        base_url = str(self._get("base_url", "")).strip()
        if not base_url:
            raise ValueError("请先在插件配置中填写 base_url")
        return base_url.rstrip("/")

    def _endpoint(self) -> str:
        api_url = str(self._get("image_api_url", "")).strip()
        if api_url:
            return api_url
        return f"{self._base_url()}/v1/images/generations"

    def _api_key(self) -> str:
        api_key = str(self._get("api_key", "")).strip()
        if not api_key:
            raise ValueError("请先在插件配置中填写 api_key")
        return api_key

    def _auth_headers(self) -> dict[str, str]:
        if str(self._get("auth_header", "Authorization")) == "x-api-key":
            return {"x-api-key": self._api_key()}
        return {"Authorization": f"Bearer {self._api_key()}"}

    def _timeout(self) -> httpx.Timeout:
        timeout_seconds = float(self._get("timeout_seconds", 180))
        return httpx.Timeout(timeout_seconds, connect=min(30.0, timeout_seconds))

    def _retry_times(self) -> int:
        try:
            return max(0, int(self._get("retry_times", 1)))
        except Exception:
            return 1

    def _proxy(self) -> str | None:
        proxy = str(self._get("proxy", "")).strip()
        return proxy or None

    def _model(self) -> str:
        model = str(self._get("model", "gpt-image-2")).strip()
        return model or "gpt-image-2"

    def _size(self) -> str:
        size = str(self._get("size", "1024x1024")).strip()
        return size or "1024x1024"

    def _response_format(self) -> str:
        response_format = str(self._get("response_format", "url")).strip()
        return response_format or "url"

    def _serialize_image_ref(self, image_ref: str) -> str:
        if image_ref.startswith("data:image/"):
            return image_ref
        if image_ref.startswith("base64://"):
            return f"data:image/png;base64,{image_ref.removeprefix('base64://')}"
        if image_ref.startswith("file:///"):
            image_path = Path(image_ref[8:])
            if image_path.exists():
                return f"data:image/png;base64,{base64.b64encode(image_path.read_bytes()).decode()}"
        if image_ref.startswith(("http://", "https://")):
            return image_ref
        image_path = Path(image_ref)
        if image_path.exists():
            return f"data:image/png;base64,{base64.b64encode(image_path.read_bytes()).decode()}"
        return image_ref

    async def generate(self, prompt: str, image_refs: list[str] | None = None) -> str:
        payload: dict[str, Any] = {
            "model": self._model(),
            "prompt": prompt,
            "size": self._size(),
            "response_format": self._response_format(),
        }
        if image_refs:
            payload["image"] = [self._serialize_image_ref(ref) for ref in image_refs if ref]

        async with httpx.AsyncClient(timeout=self._timeout(), proxy=self._proxy()) as client:
            response = await self._post_with_retry(client, self._endpoint(), json=payload)
            data = self._parse_json(response, "生成")

        return self._extract_image_output(data)

    async def _post_with_retry(self, client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        retry_times = self._retry_times()
        last_error: Exception | None = None

        for attempt in range(retry_times + 1):
            try:
                return await client.post(url, headers=self._auth_headers(), **kwargs)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                last_error = exc
                if attempt >= retry_times:
                    break
                logger.warning(
                    "生成请求失败，准备重试 (%s/%s): %s",
                    attempt + 1,
                    retry_times + 1,
                    exc,
                )
                await asyncio.sleep(min(2.0, attempt + 1))

        raise RuntimeError(f"生成请求失败：{last_error}") from last_error

    def _parse_json(self, response: httpx.Response, action: str) -> Any:
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

    def _extract_image_output(self, data: Any) -> str:
        image_url = self._find_first_value(data, "url")
        if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
            return image_url

        image_b64 = self._find_first_value(data, "b64_json")
        if isinstance(image_b64, str) and image_b64:
            return self._save_b64_image(image_b64)

        raise RuntimeError("接口返回中未找到图片结果")

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

        image_path = self._storage_dir / f"{uuid.uuid4().hex}.png"
        image_path.write_bytes(base64.b64decode(image_b64))
        return str(image_path)


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.storage_dir = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_gpt_image2"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.client = RightCodeDrawClient(config, self.storage_dir)

    @filter.command("draw", alias={"画图"})
    async def draw(self, event: AstrMessageEvent, prompt: str):
        """/draw <prompt> 生成图片；回复图片时自动传入参考图。"""
        prompt = prompt.strip()
        if not prompt:
            yield event.plain_result("用法：/draw 描述词")
            event.stop_event()
            return

        yield event.plain_result("正在生成图片，请稍等…")

        try:
            reply_images = await self._find_reply_images(event)
            image_url_or_path = await self.client.generate(prompt, image_refs=reply_images)
            yield event.image_result(image_url_or_path)
        except Exception as exc:
            logger.exception("gpt-image-2 生成失败")
            yield event.plain_result(f"生成失败：{exc}")
        finally:
            event.stop_event()

    async def _find_reply_images(self, event: AstrMessageEvent) -> list[str]:
        reply_component = self._find_reply_component(event)
        if not reply_component:
            return []

        quoted_images = await extract_quoted_message_images(event, reply_component)
        if not quoted_images:
            return []

        return [await self._normalize_image_ref(image_ref) for image_ref in quoted_images]

    def _find_reply_component(self, event: AstrMessageEvent) -> Reply | None:
        for comp in event.get_messages():
            if isinstance(comp, Reply):
                return comp
        return None

    async def _normalize_image_ref(self, image_ref: str) -> str:
        if image_ref.startswith("file:///"):
            return image_ref[8:]
        if image_ref.startswith("base64://"):
            return f"data:image/png;base64,{image_ref.removeprefix('base64://')}"
        if image_ref.startswith(("http://", "https://")):
            image = Image.fromURL(image_ref)
            return await image.convert_to_file_path()
        if image_ref.startswith("data:image/"):
            return image_ref
        path = Path(image_ref)
        if path.exists():
            return str(path.resolve())
        return image_ref
