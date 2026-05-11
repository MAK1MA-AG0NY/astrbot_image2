from __future__ import annotations

import asyncio
import base64
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Reply
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
from astrbot.core.utils.quoted_message.extractor import extract_quoted_message_images


class RightCodeScriptRunner:
    def __init__(self, config: AstrBotConfig, storage_dir: Path) -> None:
        self._config = config
        self._storage_dir = storage_dir
        self._plugin_dir = Path(__file__).resolve().parent

    def _get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def _python(self) -> str:
        python_executable = str(self._get("python_executable", sys.executable)).strip()
        return python_executable or sys.executable

    def _script_path(self) -> Path:
        script_path = str(self._get("script_path", "rcode_draw.py")).strip()
        path = Path(os.path.expanduser(script_path))
        if not path.is_absolute():
            path = self._plugin_dir / path
        if not path.is_file():
            raise FileNotFoundError(f"脚本不存在: {path}")
        return path

    def _api_key(self) -> str:
        api_key = str(self._get("api_key", "")).strip()
        if not api_key:
            api_key = str(os.environ.get("right_code_image2", "")).strip()
        if not api_key:
            raise ValueError("请先在 AstrBot 配置中填写 api_key，或设置环境变量 right_code_image2")
        return api_key

    def _model(self) -> str:
        return str(self._get("model", "gpt-image-2")).strip() or "gpt-image-2"

    def _size(self) -> str:
        return str(self._get("size", "")).strip()

    def _keep_url(self) -> bool:
        return bool(self._get("keep_url", False))

    def _output(self) -> str:
        return str(self._get("output", "")).strip()

    def _timeout_seconds(self) -> int:
        try:
            return max(1, int(self._get("timeout_seconds", 180)))
        except Exception:
            return 180

    def _ensure_output_dir(self) -> None:
        if self._keep_url():
            return

        output = self._output()
        if output:
            out_path = Path(os.path.expanduser(output))
            parent = out_path.parent if out_path.suffix else out_path
            parent.mkdir(parents=True, exist_ok=True)
            return

        default_dir = Path(os.path.expanduser("~/picture/PY_iamge2"))
        default_dir.mkdir(parents=True, exist_ok=True)

    def _normalize_image_ref(self, image_ref: str) -> str:
        if not image_ref:
            return ""
        if image_ref.startswith(("http://", "https://", "data:")):
            return image_ref
        if image_ref.startswith("base64://"):
            return f"data:image/png;base64,{image_ref.removeprefix('base64://')}"
        if image_ref.startswith("file:///"):
            return image_ref[8:]

        path = Path(os.path.expanduser(image_ref))
        if path.exists():
            return str(path)
        return image_ref

    def _build_args(self, prompt: str, image_ref: str = "") -> list[str]:
        args = [self._python(), str(self._script_path()), prompt]

        if image_ref:
            args.extend(["--image", image_ref])

        model = self._model()
        if model:
            args.extend(["--model", model])

        size = self._size()
        if size:
            args.extend(["--size", size])

        output = self._output()
        if output:
            args.extend(["--output", os.path.expanduser(output)])

        if self._keep_url():
            args.append("--keep-url")

        return args

    async def run(self, prompt: str, image_ref: str = "") -> tuple[str, str]:
        self._ensure_output_dir()

        env = os.environ.copy()
        env["right_code_image2"] = self._api_key()

        args = self._build_args(prompt, image_ref)
        logger.info("调用脚本: %s", " ".join(shlex.quote(arg) for arg in args))

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=self._timeout_seconds())
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise TimeoutError(f"脚本执行超时（>{self._timeout_seconds()}秒）")

        stdout = stdout_b.decode(errors="replace").strip()
        stderr = stderr_b.decode(errors="replace").strip()

        if process.returncode != 0:
            raise RuntimeError(self._format_error(stdout, stderr, process.returncode))

        result = self._extract_result(stdout)
        if not result:
            raise RuntimeError(self._format_error(stdout, stderr, process.returncode, missing_result=True))

        return result, stdout

    def _extract_result(self, stdout: str) -> str:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if self._keep_url():
            for line in reversed(lines):
                if line.startswith(("http://", "https://")):
                    return line

        for line in reversed(lines):
            if line.startswith("已保存:"):
                return line.split("已保存:", 1)[1].strip()

        for line in reversed(lines):
            if line.startswith(("/", "./", "../", "file://")):
                return line.removeprefix("file://")

        return ""

    def _format_error(self, stdout: str, stderr: str, returncode: int, missing_result: bool = False) -> str:
        chunks = [f"脚本返回码: {returncode}"]
        if missing_result:
            chunks.append("未从脚本输出中解析到结果")
        if stdout:
            chunks.append(f"stdout: {stdout[-2000:]}")
        if stderr:
            chunks.append(f"stderr: {stderr[-2000:]}")
        return "\n".join(chunks)


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.storage_dir = Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_gpt_image2"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.runner = RightCodeScriptRunner(config, self.storage_dir)

    @filter.command("draw", alias={"画图"})
    async def draw(self, event: AstrMessageEvent, prompt: str):
        prompt = self._resolve_prompt(event, prompt)
        if not prompt:
            yield event.plain_result("用法：/draw 描述词")
            event.stop_event()
            return

        yield event.plain_result("正在生成图片，请稍等…")

        try:
            image_ref = await self._find_reply_image(event)
            result, stdout = await self.runner.run(prompt, image_ref=image_ref)
            if result.startswith(("http://", "https://")):
                yield event.image_result(result)
            else:
                yield event.image_result(result)
            logger.info("脚本输出: %s", stdout[-2000:])
        except Exception as exc:
            logger.exception("rcode_draw 脚本调用失败")
            yield event.plain_result(f"生成失败：{exc}")
        finally:
            event.stop_event()

    async def _find_reply_image(self, event: AstrMessageEvent) -> str:
        reply_component = self._find_reply_component(event)
        if not reply_component:
            return ""

        quoted_images = await extract_quoted_message_images(event, reply_component)
        if not quoted_images:
            return ""

        return self._normalize_image_ref(quoted_images[0])

    def _normalize_image_ref(self, image_ref: str) -> str:
        if not image_ref:
            return ""
        if image_ref.startswith(("http://", "https://", "data:")):
            return image_ref
        if image_ref.startswith("base64://"):
            return f"data:image/png;base64,{image_ref.removeprefix('base64://')}"
        if image_ref.startswith("file:///"):
            return image_ref[8:]

        path = Path(os.path.expanduser(image_ref))
        if path.exists():
            return str(path)
        return image_ref

    def _find_reply_component(self, event: AstrMessageEvent) -> Reply | None:
        for comp in event.get_messages():
            if isinstance(comp, Reply):
                return comp
        return None

    def _resolve_prompt(self, event: AstrMessageEvent, prompt: str) -> str:
        prompt = prompt.strip()
        candidates: list[str] = []

        for comp in event.get_messages():
            if isinstance(comp, Reply):
                continue

            for attr in ("text", "content", "message", "raw_message"):
                value = getattr(comp, attr, None)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
                    break
            else:
                text = str(comp).strip()
                if text and text != repr(comp):
                    candidates.append(text)

        combined = " ".join(candidates).strip()
        combined = re.sub(r"^/(?:draw|画图)\s*", "", combined, count=1).strip()

        if combined and len(combined) > len(prompt):
            return combined
        return prompt
